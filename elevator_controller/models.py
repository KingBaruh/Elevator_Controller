"""Data shared by the elevator controller and its scheduling policy."""

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from time import monotonic


class Direction(Enum):
    UP = "up"
    DOWN = "down"

    @property
    def step(self) -> int:
        return 1 if self is Direction.UP else -1


class MotionState(Enum):
    STOPPED = "stopped"
    MOVING = "moving"


class DoorState(Enum):
    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"


def validate_floor(floor: int) -> None:
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1:
        raise ValueError("A floor must be a positive integer.")


def validate_time(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("Time must be a finite number from a monotonic clock.")


@dataclass(frozen=True)
class HallRequest:
    floor: int
    direction: Direction
    created_at: float = field(default_factory=monotonic)

    def __post_init__(self) -> None:
        validate_floor(self.floor)
        if not isinstance(self.direction, Direction):
            raise ValueError("A hall request must have an UP or DOWN direction.")
        validate_time(self.created_at)


@dataclass(frozen=True)
class CabinRequest:
    floor: int

    def __post_init__(self) -> None:
        validate_floor(self.floor)


@dataclass(frozen=True)
class Elevator:
    """Immutable state snapshot; the controller replaces it on events.

    While moving, current_floor is the last reported floor. last_direction is
    retained while stopped so equidistant destinations have a stable tie-break.
    """

    current_floor: int
    motion: MotionState = MotionState.STOPPED
    doors: DoorState = DoorState.CLOSED
    last_direction: Direction | None = None
    target_floor: int | None = None
    faulted: bool = False

    def __post_init__(self) -> None:
        validate_floor(self.current_floor)
        if self.target_floor is not None:
            validate_floor(self.target_floor)
        if not isinstance(self.motion, MotionState) or not isinstance(self.doors, DoorState):
            raise ValueError("Invalid motion or door state.")
        if self.last_direction is not None and not isinstance(self.last_direction, Direction):
            raise ValueError("Invalid previous direction.")
        if self.motion is MotionState.MOVING and self.doors is not DoorState.CLOSED:
            raise ValueError("The elevator cannot move with doors that are not closed.")

    @property
    def ready_to_depart(self) -> bool:
        return (
            self.motion is MotionState.STOPPED
            and self.doors is DoorState.CLOSED
            and not self.faulted
        )


@dataclass(frozen=True)
class PlannedStop:
    floor: int
    service_direction: Direction | None


@dataclass(frozen=True)
class Route:
    """One finite sweep. Updates can insert stops but cannot move its endpoint.

    A terminal pickup can serve the opposite direction to travel_direction.
    A route at the current floor has no travel direction and needs no movement.
    """

    origin_floor: int
    terminal_floor: int
    travel_direction: Direction | None
    stops: tuple[PlannedStop, ...]
    committed_pickup: HallRequest | None = None

    @property
    def next_stop(self) -> PlannedStop:
        return self.stops[0]

    def after_servicing(self, floor: int) -> "Route | None":
        """Advance only after doors open at the expected stop, not on passing it."""
        if floor != self.next_stop.floor:
            raise ValueError("Only the next planned stop can be completed.")
        if len(self.stops) == 1:
            return None
        return Route(
            origin_floor=self.origin_floor,
            terminal_floor=self.terminal_floor,
            travel_direction=self.travel_direction,
            stops=self.stops[1:],
            committed_pickup=self.committed_pickup,
        )
