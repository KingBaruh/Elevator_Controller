"""Exactly four workers: shared listener, dispatcher and two car controllers."""

from queue import Queue
from threading import Event, Thread
from time import monotonic

from elevator_controller import SchedulingPolicy
from elevator_controller.events import Shutdown
from elevator_controller.models import validate_time

from .controller import GroupCarController
from .dispatch_policy import DispatchPolicy
from .hardware import GroupHardware
from .dispatcher import ElevatorDispatcher
from .messages import GroupFault


class ElevatorGroupSystem:
    def __init__(self, number_of_floors=10, *, initial_floors=(1, 10), policy_factory=SchedulingPolicy,
                 clock=monotonic, seconds_per_floor=0.7, door_seconds=0.45, dwell_seconds=2.0,
                 operation_timeout=30.0, assignment_timeout=2.0):
        self.clock = clock
        self.hardware = GroupHardware(number_of_floors, initial_floors=initial_floors, clock=clock,
                                      seconds_per_floor=seconds_per_floor, door_seconds=door_seconds)
        self.events = Queue()
        self.car_queues = {car_id: Queue() for car_id in self.hardware.cars}
        self._stopping = Event()
        self._started = False
        self._policies = {car_id: policy_factory() for car_id in self.hardware.cars}
        self.controllers = {
            car_id: GroupCarController(car_id, hardware, number_of_floors, self.events,
                                       policy=self._policies[car_id], clock=clock,
                                       door_dwell_seconds=dwell_seconds, operation_timeout=operation_timeout)
            for car_id, hardware in self.hardware.cars.items()
        }
        dispatch_policy = DispatchPolicy(number_of_floors, seconds_per_floor=seconds_per_floor,
                                         door_seconds=door_seconds, dwell_seconds=dwell_seconds)
        self.dispatcher = ElevatorDispatcher(
            number_of_floors, self.car_queues, [controller.status() for controller in self.controllers.values()],
            self._policies, dispatch_policy, clock=clock, assignment_timeout=assignment_timeout,
        )
        self.workers = (
            Thread(target=self._listen, name="group-listener"),
            Thread(target=self.dispatcher.run, args=(self.events, self._stopping), name="group-dispatcher"),
            *(Thread(target=self._run_car, args=(car_id,), name=f"group-controller-{car_id}")
              for car_id in self.controllers),
        )

    @property
    def snapshot(self):
        return self.dispatcher.snapshot

    def _listen(self):
        try:
            while not self._stopping.is_set():
                message = self.hardware.wait_for_event()
                if self._stopping.is_set():
                    return
                if message is not None:
                    self.events.put(message)
        except Exception as error:
            if not self._stopping.is_set():
                self.events.put(GroupFault(f"Shared hardware listener failed: {error}"))

    def _run_car(self, car_id):
        try:
            self.controllers[car_id].run(self.car_queues[car_id], self._stopping)
        except Exception as error:
            if not self._stopping.is_set():
                self.events.put(GroupFault(f"Lift {car_id} worker failed: {error}"))

    def start(self):
        if self._started or self._stopping.is_set():
            raise RuntimeError("An ElevatorGroupSystem can only be started once.")
        self._started = True
        for worker in self.workers:
            worker.start()

    def stop(self, timeout=2.0):
        validate_time(timeout)
        if timeout <= 0:
            raise ValueError("Shutdown timeout must be positive.")
        # Freeze this simulated world before stopping its software workers.
        self.hardware.shutdown()
        self._stopping.set()
        self.hardware.cancel_wait()
        self.events.put(Shutdown())
        for events in self.car_queues.values():
            events.put(Shutdown())
        deadline = monotonic() + timeout
        for worker in self.workers:
            if worker.ident is not None:
                worker.join(max(0, deadline - monotonic()))
        if any(worker.is_alive() for worker in self.workers):
            raise TimeoutError("One or more group workers did not stop.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_exc):
        self.stop()
