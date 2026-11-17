"""Check the actual local executor protocol without credentials or API dependency."""
import http.client
import json
import socket
import sys


class Connection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.host)


def check(path):
    connection = Connection(path, timeout=2)
    try:
        connection.request('POST', '/health', json.dumps({'actor': 'healthcheck'}),
                           {'Content-Type': 'application/json'})
        response = connection.getresponse()
        payload = response.read(1025)
        return response.status == 200 and len(payload) <= 1024 and json.loads(payload) == {'ready': True}
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


if __name__ == '__main__':
    sys.exit(0 if check(sys.argv[1]) else 1)
