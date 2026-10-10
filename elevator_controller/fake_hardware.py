"""Manually driven test hardware. It runs no threads and models no travel time."""

from dataclasses import dataclass, replace
from queue import Queue
from threading import Condition

from .events import (
    ArrivedAtFloor, DoorsClosed, DoorsOpened, FloorPassed, HardwareEvent, HardwareFault,
)
from .models import Direction, DoorState, Elevator, MotionState
from .hardware import ElevatorHardware


@dataclass(frozen=True)
class HardwareCommand:
    sequence: int
    name: str
    arguments: tuple
    accepted: bool = True


class FakeHardware(ElevatorHardware):
    """Tests emit completion events explicitly and inspect recorded commands.

    The condition protects command acceptance and physical state together, so
    an arrival queued before a retarget causes that retarget to be rejected.
    Braking feasibility is represented by the accept_retargets test setting.
    """

    def __init__(self, initial_state: Elevator | None = None) -> None:
        self._state = initial_state if initial_state is not None else Elevator(1)
        self._events: Queue[HardwareEvent | None] = Queue()
        self._condition = Condition()
        self._commands: list[HardwareCommand] = []
        self._motion_id: int | None = None
        self._door_id: int | None = None
        self.accept_moves = True
        self.accept_retargets = True
        self.accept_doors = True

    def get_initial_state(self) -> Elevator:
        with self._condition:
            return self._state

    def wait_for_event(self) -> HardwareEvent | None:
        return self._events.get()

    def cancel_wait(self) -> None:
        self._events.put(None)

    @property
    def commands(self) -> tuple[HardwareCommand, ...]:
        with self._condition:
            return tuple(self._commands)

    def _record(self, name: str, arguments: tuple, accepted: bool = True) -> None:
        self._commands.append(HardwareCommand(len(self._commands) + 1, name, arguments, accepted))
        self._condition.notify_all()

    def wait_for_command(
        self, name: str, *, after: int = 0, timeout: float = 2.0
    ) -> HardwareCommand:
        with self._condition:
            def find():
                return next((c for c in self._commands if c.sequence > after and c.name == name), None)
            if not self._condition.wait_for(lambda: find() is not None, timeout):
                raise TimeoutError(f"No {name} command received.")
            return find()

    def move_to_floor(self, floor: int, command_id: int) -> bool:
        with self._condition:
            accepted = self.accept_moves and self._state.ready_to_depart and floor != self._state.current_floor
            if accepted:
                direction = Direction.UP if floor > self._state.current_floor else Direction.DOWN
                self._state = replace(self._state, motion=MotionState.MOVING, target_floor=floor, last_direction=direction)
                self._motion_id = command_id
            self._record("move_to_floor", (floor, command_id), accepted)
            return accepted

    def try_update_target(self, floor: int, command_id: int) -> bool:
        with self._condition:
            direction = self._state.last_direction
            accepted = (
                self.accept_retargets and not self._state.faulted
                and self._state.motion is MotionState.MOVING
                and command_id == self._motion_id and direction is not None
                and (floor - self._state.current_floor) * direction.step > 0
            )
            if accepted:
                self._state = replace(self._state, target_floor=floor)
            self._record("try_update_target", (floor, command_id), accepted)
            return accepted

    def open_doors(self, command_id: int) -> bool:
        with self._condition:
            accepted = self.accept_doors and self._state.ready_to_depart
            if accepted:
                self._state = replace(self._state, doors=DoorState.OPENING)
                self._door_id = command_id
            self._record("open_doors", (command_id,), accepted)
            return accepted

    def close_doors(self, command_id: int) -> bool:
        with self._condition:
            accepted = self.accept_doors and not self._state.faulted and self._state.doors is DoorState.OPEN
            if accepted:
                self._state = replace(self._state, doors=DoorState.CLOSING)
                self._door_id = command_id
            self._record("close_doors", (command_id,), accepted)
            return accepted

    def update_displays(self, floor: int, direction: Direction | None) -> None:
        with self._condition:
            self._record("update_displays", (floor, direction))

    def emit(self, event: HardwareEvent) -> None:
        """Complete physical actions before queuing their reports to software."""
        with self._condition:
            if isinstance(event, ArrivedAtFloor) and event.command_id == self._motion_id:
                self._state = replace(self._state, current_floor=event.floor, motion=MotionState.STOPPED, target_floor=None)
                self._motion_id = None
            elif isinstance(event, FloorPassed) and event.command_id == self._motion_id:
                self._state = replace(self._state, current_floor=event.floor)
            elif isinstance(event, DoorsOpened) and event.command_id == self._door_id:
                self._state = replace(self._state, doors=DoorState.OPEN)
                self._door_id = None
            elif isinstance(event, DoorsClosed) and event.command_id == self._door_id:
                self._state = replace(self._state, doors=DoorState.CLOSED)
                self._door_id = None
            elif isinstance(event, HardwareFault):
                self._state = replace(self._state, faulted=True)
            self._events.put(event)
