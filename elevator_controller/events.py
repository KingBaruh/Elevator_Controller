"""Messages from hardware, as distinct from outstanding passenger requests."""

from dataclasses import dataclass

from .models import Direction


@dataclass(frozen=True)
class HallButtonPressed:
    floor: int
    direction: Direction
    created_at: float | None = None


@dataclass(frozen=True)
class CabinButtonPressed:
    floor: int


@dataclass(frozen=True)
class FloorPassed:
    floor: int
    command_id: int


@dataclass(frozen=True)
class ArrivedAtFloor:
    floor: int
    command_id: int


@dataclass(frozen=True)
class DoorsOpened:
    command_id: int


@dataclass(frozen=True)
class DoorsClosed:
    command_id: int


@dataclass(frozen=True)
class HardwareFault:
    details: str


HardwareEvent = (
    HallButtonPressed | CabinButtonPressed | FloorPassed | ArrivedAtFloor
    | DoorsOpened | DoorsClosed | HardwareFault
)


@dataclass(frozen=True)
class Shutdown:
    """Internal wake-up message, never produced by the hardware."""


ControlMessage = HardwareEvent | Shutdown
