"""Execution-owned local monitoring that does not wait for recovery HTTP."""
import logging
import math
from pathlib import Path
import threading
import time

from .recovery_runtime import RecoveryRuntime
from .recovery_supervisor import RecoverySupervisor

LOG = logging.getLogger(__name__)


class RecoveryMonitor:
    def __init__(self, worker, launch, *, interval=.5, clock=time.monotonic):
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
            raise ValueError('positive finite monitoring interval required')
        state = worker.state
        claim = state['recovery_assignment']['claim']
        receipt = claim['receipt']
        proof = state['recovery']
        execution = state['task']['execution_id']
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if (launch['phase'] != 'bound' or proof['identity'] != launch['identity']
                or proof['generation'] != receipt['generation']
                or launch['assignment']['execution_id'] != execution
                or launch['assignment']['generation'] != proof['generation']
                or launch['assignment']['request_id'] != receipt['request_id']
                or claim['slot_id'] != worker.slot_id or claim['boot_id'] != boot
                or proof['identity']['boot_id'] != boot):
            raise ValueError('monitor must own the original bound execution')
        self.runtime = RecoveryRuntime(worker, execution)
        self.clock, self.interval = clock, interval
        self.stopping = threading.Event()
        self._network = threading.Lock()
        self.last_error = None
        self.supervisor = RecoverySupervisor(Path(str(worker.path)+'.supervision.json'),
            execution_id=execution, generation=proof['generation'], process_identity=proof['identity'],
            tolerance=receipt['policy']['disconnect_tolerance_seconds'],
            started_at=launch['started_at'], requested_at=claim['requested_at'],
            timeout_seconds=state['task'].get('timeout_seconds', 0))
        self.thread = threading.Thread(target=self._watch, daemon=True, name='recovery-monitor-'+execution)
        try:
            self.thread.start()
        except BaseException:
            self.supervisor.close()
            raise

    def _watch(self):
        while not self.stopping.is_set():
            try:
                self.supervisor.tick(self.runtime.inspect, self.runtime.stop, at=self.clock())
                self.last_error = None
            except Exception as exc:
                # A failed inspection is not proof of exit or permission to
                # stop another process. Keep the journal and retry locally.
                self.last_error = exc
                LOG.exception('recovery runtime observation failed; retaining execution')
            self.stopping.wait(self.interval)

    def snapshot(self):
        return self.supervisor.snapshot()

    def exchange(self, api):
        """Caller handles bounded retry; local monitoring continues meanwhile."""
        if not self._network.acquire(blocking=False):
            raise RuntimeError('recovery monitor exchange already in progress')
        try:
            if self.stopping.is_set():
                raise RuntimeError('recovery monitor is closing')
            return self.supervisor.exchange(api, clock=self.clock)
        finally:
            self._network.release()

    def close(self, timeout=20):
        """Release supervision only after both owners stop; never kill runtime."""
        self.stopping.set()
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            raise RuntimeError('local recovery observation has not stopped')
        if not self._network.acquire(blocking=False):
            raise RuntimeError('recovery exchange has not finished; ownership retained')
        try:
            self.supervisor.close()
        finally:
            self._network.release()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
