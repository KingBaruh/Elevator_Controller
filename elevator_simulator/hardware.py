"""Time-based simulated hardware; no Tk imports and no extra worker thread."""

from collections import deque
from dataclasses import dataclass
from math import ceil, floor, isfinite
from queue import Queue
from threading import Lock
from time import monotonic
from typing import Callable

from elevator_controller.events import (
    ArrivedAtFloor, CabinButtonPressed, DoorsClosed, DoorsOpened, FloorPassed,
    HallButtonPressed, HardwareEvent,
)
from elevator_controller.models import Direction, DoorState, Elevator, MotionState, validate_floor
from elevator_controller.hardware import ElevatorHardware


@dataclass(frozen=True)
class Activity:
    sequence: int
    elapsed: float
    text: str


@dataclass(frozen=True)
class SimulationSnapshot:
    position: float
    current_floor: int
    target_floor: int | None
    motion: MotionState
    direction: Direction | None
    doors: DoorState
    door_open_fraction: float
    display_floor: int
    display_direction: Direction | None
    activity: tuple[Activity, ...]


class SimulatorHardware(ElevatorHardware):
    """Implements ElevatorHardware using a continuously changing position.

    The GUI calls tick() on its event loop. Commands also advance to the current
    time before deciding acceptance, so delayed screen refreshes cannot allow a
    stop behind the physical cabin. One lock serializes time, command acceptance
    and completion-event publication. The event stream has its own queue.

    Constant travel speed and a minimum retarget distance are a visualization
    model, not a physical braking/acceleration model.
    """

    def __init__(
        self,
        number_of_floors: int = 10,
        *,
        seconds_per_floor: float = 0.7,
        door_seconds: float = 0.45,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        validate_floor(number_of_floors)
        for value in (seconds_per_floor, door_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError("Simulation durations must be finite and positive.")
        self.number_of_floors = number_of_floors
        self.seconds_per_floor = seconds_per_floor
        self.door_seconds = door_seconds
        self.minimum_stop_distance = 0.25
        self._clock = clock
        self._lock = Lock()
        self._events: Queue[HardwareEvent | None] = Queue()
        self._started_at = clock()
        self._last_update = self._started_at
        self._position = 1.0
        self._current_floor = 1
        self._target: int | None = None
        self._direction: Direction | None = None
        self._motion_id: int | None = None
        self._doors = DoorState.CLOSED
        self._door_fraction = 0.0
        self._door_id: int | None = None
        self._display_floor = 1
        self._display_direction: Direction | None = None
        self._closed = False
        self._activity: deque[Activity] = deque(maxlen=100)
        self._sequence = 0
        self._record("Ready at floor 1.")

    def _record(self, text: str) -> None:
        self._sequence += 1
        self._activity.append(Activity(self._sequence, self._clock() - self._started_at, text))

    def _check_floor(self, value: int) -> None:
        validate_floor(value)
        if value > self.number_of_floors:
            raise ValueError("Floor is outside the simulated building.")

    def get_initial_state(self) -> Elevator:
        with self._lock:
            return Elevator(
                current_floor=self._current_floor,
                motion=MotionState.MOVING if self._motion_id is not None else MotionState.STOPPED,
                doors=self._doors,
                last_direction=self._direction,
                target_floor=self._target,
            )

    def wait_for_event(self) -> HardwareEvent | None:
        return self._events.get()

    def cancel_wait(self) -> None:
        self._events.put(None)

    def press_hall(self, floor_number: int, direction: Direction) -> None:
        self._check_floor(floor_number)
        if not isinstance(direction, Direction):
            raise ValueError("A hall call needs an UP or DOWN direction.")
        if (floor_number == 1 and direction is Direction.DOWN) or (
            floor_number == self.number_of_floors and direction is Direction.UP
        ):
            raise ValueError("This direction is unavailable at the end of the building.")
        with self._lock:
            if not self._closed:
                self._advance(self._clock())
                self._events.put(HallButtonPressed(floor_number, direction, self._clock()))
                self._record(f"Floor {floor_number} called {direction.value}.")

    def press_cabin(self, floor_number: int) -> None:
        self._check_floor(floor_number)
        with self._lock:
            if not self._closed:
                self._advance(self._clock())
                self._events.put(CabinButtonPressed(floor_number))
                self._record(f"Cabin selected floor {floor_number}.")

    def move_to_floor(self, floor: int, command_id: int) -> bool:
        self._check_floor(floor)
        with self._lock:
            self._advance(self._clock())
            if self._closed or self._motion_id is not None or self._doors is not DoorState.CLOSED:
                return False
            if floor == self._current_floor:
                return False
            self._target = floor
            self._direction = Direction.UP if floor > self._position else Direction.DOWN
            self._motion_id = command_id
            self._record(f"Moving {self._direction.value} to floor {floor}.")
            return True

    def try_update_target(self, floor: int, command_id: int) -> bool:
        self._check_floor(floor)
        with self._lock:
            self._advance(self._clock())
            if self._closed or self._motion_id != command_id or self._direction is None:
                return False
            assert self._target is not None
            distance = (floor - self._position) * self._direction.step
            remaining = (self._target - self._position) * self._direction.step
            extending = distance > remaining
            if floor != self._target and (
                distance < self.minimum_stop_distance
                or (extending and remaining < self.minimum_stop_distance)
            ):
                self._record(f"Too late to change the stop: floor {floor} stays pending.")
                return False
            self._target = floor
            if extending:
                self._record(f"Pickup approach extended to floor {floor}.")
            else:
                self._record(f"Intermediate stop accepted: floor {floor}.")
            return True

    def open_doors(self, command_id: int) -> bool:
        with self._lock:
            self._advance(self._clock())
            if self._closed or self._motion_id is not None or self._doors is not DoorState.CLOSED:
                return False
            self._doors = DoorState.OPENING
            self._door_id = command_id
            self._record("Opening doors.")
            return True

    def close_doors(self, command_id: int) -> bool:
        with self._lock:
            self._advance(self._clock())
            if self._closed or self._motion_id is not None or self._doors is not DoorState.OPEN:
                return False
            self._doors = DoorState.CLOSING
            self._door_id = command_id
            self._record("Closing doors.")
            return True

    def update_displays(self, floor: int, direction: Direction | None) -> None:
        self._check_floor(floor)
        with self._lock:
            self._display_floor = floor
            self._display_direction = direction

    def _advance(self, now: float) -> None:
        elapsed = max(0.0, now - self._last_update)
        self._last_update = now
        if self._closed:
            return
        if self._motion_id is not None:
            assert self._direction is not None and self._target is not None
            step = self._direction.step
            destination = self._position + step * elapsed / self.seconds_per_floor
            position = min(destination, self._target) if step > 0 else max(destination, self._target)
            # Even a delayed frame must report every crossed intermediate floor.
            boundary = floor(self._position) + 1 if step > 0 else ceil(self._position) - 1
            while (position - boundary) * step >= 0 and boundary != self._target:
                self._current_floor = boundary
                self._events.put(FloorPassed(boundary, self._motion_id))
                boundary += step
            self._position = position
            if position == self._target:
                command_id = self._motion_id
                self._current_floor = self._target
                self._target = None
                self._motion_id = None
                self._direction = None
                self._events.put(ArrivedAtFloor(self._current_floor, command_id))
                self._record(f"Arrived at floor {self._current_floor}.")
        elif self._door_id is not None:
            opening = self._doors is DoorState.OPENING
            change = elapsed / self.door_seconds * (1 if opening else -1)
            self._door_fraction = max(0.0, min(1.0, self._door_fraction + change))
            if opening and self._door_fraction >= 1:
                self._doors = DoorState.OPEN
                self._events.put(DoorsOpened(self._door_id))
                self._door_id = None
                self._record("Doors open. Choose a cabin destination.")
            elif not opening and self._door_fraction <= 0:
                self._doors = DoorState.CLOSED
                self._events.put(DoorsClosed(self._door_id))
                self._door_id = None
                self._record("Doors closed.")

    def tick(self) -> None:
        """Advance from the UI event loop, or from a manual clock in tests."""
        with self._lock:
            self._advance(self._clock())

    @property
    def snapshot(self) -> SimulationSnapshot:
        with self._lock:
            return SimulationSnapshot(
                position=self._position, current_floor=self._current_floor,
                target_floor=self._target,
                motion=MotionState.MOVING if self._motion_id is not None else MotionState.STOPPED,
                direction=self._direction, doors=self._doors,
                door_open_fraction=self._door_fraction,
                display_floor=self._display_floor, display_direction=self._display_direction,
                activity=tuple(self._activity),
            )

    def shutdown(self) -> None:
        """Freeze this simulated world before its controller workers are stopped."""
        with self._lock:
            self._closed = True
