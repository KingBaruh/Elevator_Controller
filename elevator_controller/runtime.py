"""Lifecycle for exactly two worker threads, with explicit wake-up on shutdown."""

from queue import Queue
from threading import Event, Thread
from time import monotonic

from .controller import ElevatorController
from .events import ControlMessage, Shutdown
from .hardware import ElevatorHardware
from .listener import HardwareListener
from .models import validate_time
from .scheduler import SchedulingPolicy


class ElevatorSystem:
    """Single-use runtime; start/stop are called by the owning main thread.

    stop() shuts down software workers, not the physical drive. Normal use stops
    after service becomes idle. Physical stopping/interlocks belong to hardware.
    """

    def __init__(
        self,
        hardware: ElevatorHardware,
        number_of_floors: int,
        *,
        policy: SchedulingPolicy | None = None,
        door_dwell_seconds: float = 3.0,
        operation_timeout: float = 30.0,
    ) -> None:
        self._hardware = hardware
        self._events: Queue[ControlMessage] = Queue()
        self._stopping = Event()
        self.controller = ElevatorController(
            hardware, number_of_floors, policy=policy,
            door_dwell_seconds=door_dwell_seconds, operation_timeout=operation_timeout,
        )
        listener = HardwareListener(hardware, self._events, self._stopping)
        self.workers = (
            Thread(target=listener.run, name="elevator-listener"),
            Thread(target=self.controller.run, args=(self._events, self._stopping), name="elevator-controller"),
        )
        self._started = False

    def start(self) -> None:
        if self._started or self._stopping.is_set():
            raise RuntimeError("An ElevatorSystem can only be started once.")
        self._started = True
        for worker in self.workers:
            worker.start()

    def stop(self, timeout: float = 2.0) -> None:
        validate_time(timeout)
        if timeout <= 0:
            raise ValueError("Shutdown timeout must be positive.")
        self._stopping.set()
        self._events.put(Shutdown())
        self._hardware.cancel_wait()
        deadline = monotonic() + timeout
        for worker in self.workers:
            if worker.ident is not None:
                worker.join(max(0.0, deadline - monotonic()))
        if any(worker.is_alive() for worker in self.workers):
            raise TimeoutError("Hardware did not release its listener during shutdown.")

    def __enter__(self) -> "ElevatorSystem":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()
