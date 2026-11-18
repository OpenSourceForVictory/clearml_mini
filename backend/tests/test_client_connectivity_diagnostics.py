import asyncio
import logging
import socket
import ssl

import pytest
from aiohttp import web, WSServerHandshakeError
from worker import client_connectivity as guard


def test_head_405_logged_and_proxy_ignored(monkeypatch, caplog):
    monkeypatch.setattr('runner.cs_contract.check_ip', lambda host: None)
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    caplog.set_level(logging.INFO)
    async def run():
        async def handler(request):
            assert request.method == 'HEAD'
            return web.Response(status=405)
        app = web.Application(); app.router.add_route('*', '/', handler)
        runner = web.AppRunner(app); await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            await guard.check(f'ws://127.0.0.1:{port}/')
        finally:
            await runner.cleanup()
    asyncio.run(run())
    assert 'status=405' in caplog.text
    assert 'method=HEAD' in caplog.text
    assert 'elapsed_ms=' in caplog.text
    assert 'connectivity passed' in caplog.text


@pytest.mark.parametrize('exc,reason', [
    (TimeoutError('secret'), 'timeout'),
    (ConnectionRefusedError(111, 'secret'), 'connection_refused'),
    (socket.gaierror(-2, 'secret'), 'dns'),
    (ssl.SSLError('secret'), 'tls'),
    (WSServerHandshakeError(None, (), status=405, message='secret'), 'websocket_handshake'),
    (ValueError('secret'), 'invalid_endpoint'),
    (ImportError('secret'), 'dependency'),
    (OSError('secret'), 'connection_error'),
    (RuntimeError('secret'), 'unexpected_error'),
])
def test_failure_logged_and_reported_without_secrets(monkeypatch, caplog, exc, reason):
    async def fail(endpoint, timeout):
        raise exc
    monkeypatch.setattr(guard, '_probe', fail)
    with pytest.raises(RuntimeError) as error:
        guard.verify('http://user:password@example.com/private?token=secret#fragment', timeout=.2)
    for message in (caplog.text, str(error.value)):
        assert f'reason={reason}' in message
        assert 'timeout_s=0.2' in message
        assert 'target=http://example.com' in message
        for hidden in ('password', 'private', 'secret', 'fragment', 'user:'):
            assert hidden not in message
    assert 'exception=' in caplog.text
    if reason == 'websocket_handshake':
        assert 'status=405' in caplog.text


def test_cancel_not_reported_as_connectivity_failure(monkeypatch, caplog):
    async def cancel(endpoint, timeout):
        raise asyncio.CancelledError()
    monkeypatch.setattr(guard, '_probe', cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(guard.check('http://example.com'))
    assert not caplog.records

@pytest.mark.parametrize('endpoint,expected', [
    ('http://[::1]:9000/private', 'http://[::1]:9000'),
    ('http://[invalid', '<invalid>'),
    ('ftp://example.com', '<invalid>'),
    ('http://example.com:bad', '<invalid>'),
    ('http://exa mple.com', '<invalid>'),
])
def test_safe_target(endpoint, expected):
    assert guard.target(endpoint) == expected


def test_verify_success_and_default_timeout(monkeypatch, caplog):
    async def ok(endpoint, timeout):
        assert timeout == 3
        return 'HEAD', 405
    monkeypatch.setattr(guard, '_probe', ok)
    caplog.set_level(logging.INFO)
    assert guard.verify('http://example.com') is None
    assert 'status=405' in caplog.text


def test_connector_os_error_classification():
    from aiohttp import ClientConnectorError
    assert 'reason=connection_refused' in guard.diagnosis(ClientConnectorError(None, ConnectionRefusedError(111, 'hidden')))
    assert 'reason=dns' in guard.diagnosis(ClientConnectorError(None, socket.gaierror(-2, 'hidden')))
