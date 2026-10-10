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

    # The Ctor
    def __init__(
        self,
        hardware: ElevatorHardware, # The hardware
        number_of_floors: int, # The number of floors in the building
        *,
        policy: SchedulingPolicy | None = None, # The scheduling policy to use; defaults to the default policy
        door_dwell_seconds: float = 3.0, # The time to keep doors open after arriving at a floor
        operation_timeout: float = 30.0, # The maximum time to wait for a hardware operation to complete
    ) -> None:

        # Validate the input parameters.
        if number_of_floors <= 0:
            raise ValueError("Number of floors must be positive.")
        if door_dwell_seconds <= 0:
            raise ValueError("Door dwell time must be positive.")
        if operation_timeout <= 0:
            raise ValueError("Operation timeout must be positive.")

        # Hardware is not thread-safe; only the controller and listener threads may call it.
        self._hardware = hardware

        # The controller and listener threads communicate via a queue of ControlMessages.
        self._events: Queue[ControlMessage] = Queue()

        # The controller and listener threads are stopped by setting this event and
        self._stopping = Event()

        # The controller is the only thread that mutates elevator state, so it owns the policy.
        self.controller = ElevatorController(
            hardware, number_of_floors, policy=policy,
            door_dwell_seconds=door_dwell_seconds, operation_timeout=operation_timeout,
        )

        # The listener is the only thread that calls hardware.wait_for_event(), so it owns the hardware.
        listener = HardwareListener(hardware, self._events, self._stopping)

        # The two threads are started and stopped together; the main thread may wait for them to finish.
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
