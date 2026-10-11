"""Two existing simulated drives feeding one shared event source.

The GUI (or a test clock) ticks the simulation. Each car buffers completion
events in its existing queue; tick forwards them with a car ID. The single
group listener blocks on the shared queue. No per-car listener is created.
"""

from queue import Empty, Queue
from threading import Lock
from time import monotonic

from elevator_controller import Direction
from elevator_controller.events import CabinButtonPressed, HallButtonPressed, HardwareFault
from elevator_controller.models import HallRequest, validate_floor
from elevator_simulator.hardware import SimulatorHardware

from .messages import CarEvent


class CarHardware(SimulatorHardware):
    def __init__(self, number_of_floors, *, initial_floor, **options):
        super().__init__(number_of_floors, **options)
        self._check_floor(initial_floor)
        # Initialization only, before any worker is started.
        self._position = float(initial_floor)
        self._current_floor = self._display_floor = initial_floor
        self._activity.clear()
        self._record(f"Ready at floor {initial_floor}.")

    def drain_events(self):
        """Expose nonblocking buffered events to the group simulation clock."""
        while True:
            try:
                event = self._events.get_nowait()
            except Empty:
                return
            if event is not None:
                yield event


class GroupHardware:
    def __init__(self, number_of_floors=10, *, initial_floors=(1, 10), clock=monotonic,
                 seconds_per_floor=0.7, door_seconds=0.45):
        validate_floor(number_of_floors)
        if len(initial_floors) != 2:
            raise ValueError("Exactly two initial floors are required.")
        self.number_of_floors = number_of_floors
        self.clock = clock
        self._events = Queue()
        self._input_lock = Lock()
        self._closed = False
        self.cars = {
            car_id: CarHardware(number_of_floors, initial_floor=floor, clock=clock,
                                seconds_per_floor=seconds_per_floor, door_seconds=door_seconds)
            for car_id, floor in zip(("A", "B"), initial_floors)
        }

    def _check_floor(self, floor):
        validate_floor(floor)
        if floor > self.number_of_floors:
            raise ValueError("Floor is outside the building.")

    def _check_car(self, elevator_id):
        if elevator_id not in self.cars:
            raise ValueError("Unknown elevator ID.")

    def press_hall(self, floor, direction):
        self._check_floor(floor)
        request = HallRequest(floor, direction, self.clock())
        if (floor == 1 and direction is Direction.DOWN) or (
                floor == self.number_of_floors and direction is Direction.UP):
            raise ValueError("No hall button in this direction at the end of the building.")
        with self._input_lock:
            if not self._closed:
                self._events.put(HallButtonPressed(request.floor, request.direction, request.created_at))

    def press_cabin(self, elevator_id, floor):
        self._check_car(elevator_id)
        self._check_floor(floor)
        with self._input_lock:
            if not self._closed:
                self._events.put(CarEvent(elevator_id, CabinButtonPressed(floor)))

    def fail_car(self, elevator_id):
        """Freeze this simulated car, then notify its controller of the fault.

        This is a demonstration of a disabled simulated drive, not a physical
        emergency-stop command or a recovery procedure for trapped passengers.
        """
        self._check_car(elevator_id)
        with self._input_lock:
            if not self._closed:
                self.cars[elevator_id].shutdown()
                self._events.put(CarEvent(elevator_id, HardwareFault("Simulated drive disabled.")))

    def tick(self):
        with self._input_lock:
            if self._closed:
                return
            for car_id, hardware in self.cars.items():
                hardware.tick()
                for event in hardware.drain_events():
                    self._events.put(CarEvent(car_id, event))

    def wait_for_event(self):
        return self._events.get()

    def cancel_wait(self):
        self._events.put(None)

    def drain_events(self):
        """For deterministic tests only; never use alongside the listener."""
        while True:
            try:
                event = self._events.get_nowait()
            except Empty:
                return
            if event is not None:
                yield event

    def shutdown(self):
        with self._input_lock:
            self._closed = True
            for hardware in self.cars.values():
                hardware.shutdown()
