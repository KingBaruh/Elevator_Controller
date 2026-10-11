"""Immutable messages between the shared listener, dispatcher and car workers."""

from dataclasses import dataclass

from elevator_controller import ControllerSnapshot, HallRequest
from elevator_controller.events import HardwareEvent


@dataclass(frozen=True)
class HallCall:
    request_id: int
    request: HallRequest
    elevator_id: str | None = None
    assignment_id: int | None = None
    state: str = "waiting"
    sent_at: float | None = None


@dataclass(frozen=True)
class CarEvent:
    elevator_id: str
    event: HardwareEvent


@dataclass(frozen=True)
class CarStatus:
    elevator_id: str
    controller: ControllerSnapshot
    reported_at: float
    position: float
    door_open_fraction: float
    dwell_deadline: float | None


@dataclass(frozen=True)
class CarReport:
    status: CarStatus
    handled_cabin: int | None = None


@dataclass(frozen=True)
class AssignHallCall:
    call: HallCall


@dataclass(frozen=True)
class AssignmentAck:
    elevator_id: str
    request_id: int
    assignment_id: int
    accepted: bool


@dataclass(frozen=True)
class HallCallServed:
    elevator_id: str
    request_id: int
    assignment_id: int
    served_at: float


@dataclass(frozen=True)
class ReleaseHallCalls:
    """Only a fault-latched controller may relinquish its hall assignments."""


@dataclass(frozen=True)
class AssignmentsReleased:
    elevator_id: str
    assignments: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class GroupFault:
    details: str


@dataclass(frozen=True)
class GroupActivity:
    sequence: int
    timestamp: float
    text: str


@dataclass(frozen=True)
class ServiceRecord:
    elevator_id: str
    request_id: int
    floor: int
    direction: str
    waited_seconds: float


@dataclass(frozen=True)
class GroupSnapshot:
    calls: tuple[HallCall, ...]
    cars: tuple[CarStatus, ...]
    activity: tuple[GroupActivity, ...]
    services: tuple[ServiceRecord, ...]
    completed_calls: int
    error: str | None

    @property
    def idle(self) -> bool:
        return not self.error and not self.calls and all(car.controller.idle for car in self.cars)
