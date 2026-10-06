"""Single-owner controller: event handling is testable without starting threads."""

from dataclasses import dataclass, replace
from queue import Empty, Queue
from threading import Condition, Event
from time import monotonic
from typing import Callable

from .events import (
    ArrivedAtFloor, CabinButtonPressed, ControlMessage, DoorsClosed, DoorsOpened,
    FloorPassed, HallButtonPressed, HardwareEvent, HardwareFault, Shutdown,
)
from .hardware import ElevatorHardware
from .models import (
    CabinRequest, Direction, DoorState, Elevator, HallRequest, MotionState,
    PlannedStop, Route, validate_floor, validate_time,
)
from .requests import PendingRequests
from .scheduler import SchedulingPolicy


@dataclass(frozen=True)
class ControllerSnapshot:
    """Immutable view for observers; pending collections are never shared."""

    elevator: Elevator
    route: Route | None
    service_stop: PlannedStop | None
    hall_requests: tuple[HallRequest, ...]
    cabin_requests: tuple[CabinRequest, ...]
    fault: str | None
    rejected_input: str | None

    @property
    def idle(self) -> bool:
        return (
            self.elevator.ready_to_depart
            and self.route is None and self.service_stop is None
            and not self.hall_requests and not self.cabin_requests
        )


class ElevatorController:
    """Only its control worker may call handle_event/check_timeouts/run.

    Direct event handling is also supported for deterministic unit tests, when
    no worker is running. Observers use snapshot/wait_for, never mutate state.
    """

    def __init__(
        self,
        hardware: ElevatorHardware,
        number_of_floors: int,
        *,
        policy: SchedulingPolicy | None = None,
        door_dwell_seconds: float = 3.0,
        operation_timeout: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        for value in (door_dwell_seconds, operation_timeout):
            validate_time(value)
            if value <= 0:
                raise ValueError("Dwell time and operation timeout must be positive.")
        self._hardware = hardware
        self._pending = PendingRequests(number_of_floors)
        self._policy = policy if policy is not None else SchedulingPolicy()
        self._clock = clock
        self._dwell_seconds = door_dwell_seconds
        self._operation_timeout = operation_timeout
        self._elevator = hardware.get_initial_state()
        self._check_floor(self._elevator.current_floor)
        if not self._elevator.ready_to_depart or self._elevator.target_floor is not None:
            raise ValueError("Start stationary at a known floor with closed doors and no fault.")
        self._route: Route | None = None
        self._service_stop: PlannedStop | None = None
        self._motion_id: int | None = None
        self._door_id: int | None = None
        self._next_id = 1
        self._dwell_deadline: float | None = None
        self._operation_deadline: float | None = None
        self._fault: str | None = None
        self._rejected_input: str | None = None
        self._changed = Condition()
        self._snapshot = self._make_snapshot()

    def _check_floor(self, floor: int) -> None:
        validate_floor(floor)
        if floor > self._pending.number_of_floors:
            raise ValueError("Floor is outside the building.")

    def _make_snapshot(self) -> ControllerSnapshot:
        return ControllerSnapshot(
            self._elevator, self._route, self._service_stop,
            self._pending.hall, self._pending.cabin, self._fault, self._rejected_input,
        )

    def _publish(self) -> None:
        with self._changed:
            self._snapshot = self._make_snapshot()
            self._changed.notify_all()

    @property
    def snapshot(self) -> ControllerSnapshot:
        with self._changed:
            return self._snapshot

    def wait_for(
        self, predicate: Callable[[ControllerSnapshot], bool], timeout: float = 2.0
    ) -> ControllerSnapshot:
        """Wait for an observable state without polling or exposing mutable data."""
        with self._changed:
            if not self._changed.wait_for(lambda: predicate(self._snapshot), timeout):
                raise TimeoutError("The requested controller state was not reached.")
            return self._snapshot

    def _command_id(self) -> int:
        result = self._next_id
        self._next_id += 1
        return result

    def _fail(self, details: str) -> None:
        # Do not claim the physical elevator stopped just because software faulted.
        self._fault = details
        self._elevator = replace(self._elevator, faulted=True)
        self._operation_deadline = None
        self._dwell_deadline = None

    def _display(self) -> None:
        direction = None
        if self._elevator.motion is MotionState.MOVING:
            direction = self._elevator.last_direction
        elif self._service_stop is not None:
            direction = self._service_stop.service_direction
        self._hardware.update_displays(self._elevator.current_floor, direction)

    def handle_event(self, event: HardwareEvent) -> None:
        """Handle one message. Hardware/sequence errors latch a visible fault."""
        try:
            if isinstance(event, (HallButtonPressed, CabinButtonPressed)):
                self._handle_button(event)
            elif isinstance(event, HardwareFault):
                self._fail(event.details)
            elif not self._elevator.faulted:
                if isinstance(event, FloorPassed):
                    self._floor_passed(event)
                elif isinstance(event, ArrivedAtFloor):
                    self._arrived(event)
                elif isinstance(event, DoorsOpened):
                    self._doors_opened(event)
                elif isinstance(event, DoorsClosed):
                    self._doors_closed(event)
                else:
                    raise ValueError(f"Unknown event: {event!r}")
        except Exception as error:
            self._fail(f"Event handling failed: {error}")
        finally:
            self._publish()

    def _handle_button(self, event: HallButtonPressed | CabinButtonPressed) -> None:
        # Bad passenger input is rejected without disabling valid service.
        try:
            if isinstance(event, HallButtonPressed):
                timestamp = self._clock() if event.created_at is None else event.created_at
                request = HallRequest(event.floor, event.direction, timestamp)
                if timestamp > self._clock():
                    raise ValueError("Request timestamp is ahead of the controller clock.")
                added = self._pending.add_hall(request)
            else:
                added = self._pending.add_cabin(CabinRequest(event.floor))
        except ValueError as error:
            self._rejected_input = str(error)
            return
        if not added or self._elevator.faulted:
            return
        if self._elevator.doors is DoorState.OPEN and self._service_stop is not None:
            self._pending.mark_served(
                self._elevator.current_floor, self._service_stop.service_direction
            )
        self._refresh_route()
        self._advance_if_ready()

    def _refresh_route(self) -> None:
        if self._route is None or self._elevator.faulted:
            return
        proposal = self._policy.propose_route_update(
            self._route, self._elevator, self._pending, now=self._clock()
        )
        if self._elevator.motion is MotionState.MOVING:
            if proposal.next_stop.floor != self._route.next_stop.floor:
                assert self._motion_id is not None
                if not self._hardware.try_update_target(proposal.next_stop.floor, self._motion_id):
                    return  # The old route and requests remain intact.
                self._elevator = replace(self._elevator, target_floor=proposal.next_stop.floor)
                # Retargeting never extends the existing movement deadline.
        self._route = proposal

    def _advance_if_ready(self) -> None:
        if not self._elevator.ready_to_depart or self._service_stop is not None:
            return
        if self._route is None:
            self._route = self._policy.plan_next_route(
                self._elevator, self._pending, now=self._clock()
            )
        else:
            self._refresh_route()
        if self._route is None:
            self._display()
            return
        target = self._route.next_stop.floor
        if target == self._elevator.current_floor:
            self._begin_open()
            return
        command_id = self._command_id()
        if not self._hardware.move_to_floor(target, command_id):
            self._fail("Hardware rejected the movement command.")
            return
        direction = Direction.UP if target > self._elevator.current_floor else Direction.DOWN
        self._elevator = replace(
            self._elevator, motion=MotionState.MOVING,
            target_floor=target, last_direction=direction,
        )
        self._motion_id = command_id
        self._operation_deadline = self._clock() + self._operation_timeout
        self._display()

    def _floor_passed(self, event: FloorPassed) -> None:
        if event.command_id != self._motion_id:
            return
        self._check_floor(event.floor)
        direction = self._elevator.last_direction
        target = self._elevator.target_floor
        assert direction is not None and target is not None
        progress = (event.floor - self._elevator.current_floor) * direction.step
        if progress == 0:
            return  # Duplicate sensor report, not a fresh stop opportunity.
        if progress < 0 or (target - event.floor) * direction.step <= 0:
            raise ValueError("Unexpected floor report for the active movement.")
        self._elevator = replace(self._elevator, current_floor=event.floor)
        self._display()

    def _arrived(self, event: ArrivedAtFloor) -> None:
        if event.command_id != self._motion_id:
            return
        self._check_floor(event.floor)
        if event.floor != self._elevator.target_floor:
            raise ValueError("Arrival does not match the accepted target.")
        self._motion_id = None
        self._operation_deadline = None
        self._elevator = replace(
            self._elevator, current_floor=event.floor,
            motion=MotionState.STOPPED, target_floor=None,
        )
        self._begin_open()

    def _begin_open(self) -> None:
        assert self._route is not None
        self._service_stop = self._route.next_stop
        command_id = self._command_id()
        if not self._hardware.open_doors(command_id):
            self._fail("Hardware rejected the door-open command.")
            return
        self._door_id = command_id
        self._elevator = replace(self._elevator, doors=DoorState.OPENING)
        self._operation_deadline = self._clock() + self._operation_timeout
        self._display()

    def _doors_opened(self, event: DoorsOpened) -> None:
        if event.command_id != self._door_id:
            return
        if self._elevator.doors is not DoorState.OPENING:
            raise ValueError("Unexpected door-open completion.")
        assert self._service_stop is not None and self._route is not None
        self._elevator = replace(self._elevator, doors=DoorState.OPEN)
        self._door_id = None
        self._operation_deadline = None
        self._pending.mark_served(self._elevator.current_floor, self._service_stop.service_direction)
        self._route = self._route.after_servicing(self._elevator.current_floor)
        self._dwell_deadline = self._clock() + self._dwell_seconds

    def _begin_close(self) -> None:
        command_id = self._command_id()
        if not self._hardware.close_doors(command_id):
            self._fail("Hardware rejected the door-close command.")
            return
        self._door_id = command_id
        self._elevator = replace(self._elevator, doors=DoorState.CLOSING)
        self._dwell_deadline = None
        self._operation_deadline = self._clock() + self._operation_timeout

    def _doors_closed(self, event: DoorsClosed) -> None:
        if event.command_id != self._door_id:
            return
        if self._elevator.doors is not DoorState.CLOSING:
            raise ValueError("Unexpected door-close completion.")
        self._elevator = replace(self._elevator, doors=DoorState.CLOSED)
        self._door_id = None
        self._operation_deadline = None
        self._service_stop = None
        self._advance_if_ready()

    def check_timeouts(self) -> None:
        """Called between messages, including when the event queue is busy."""
        try:
            if self._elevator.faulted:
                return
            now = self._clock()
            if self._operation_deadline is not None and now >= self._operation_deadline:
                self._fail("Hardware operation timed out before completion.")
            elif self._dwell_deadline is not None and now >= self._dwell_deadline:
                self._begin_close()
        except Exception as error:
            self._fail(f"Timed action failed: {error}")
        finally:
            self._publish()

    def _wait_timeout(self) -> float | None:
        deadlines = [d for d in (self._dwell_deadline, self._operation_deadline) if d is not None]
        return max(0.0, min(deadlines) - self._clock()) if deadlines else None

    def run(self, events: Queue[ControlMessage], stopping: Event) -> None:
        try:
            self._display()
        except Exception as error:
            self._fail(f"Initial display update failed: {error}")
        self._publish()
        while not stopping.is_set():
            try:
                event = events.get(timeout=self._wait_timeout())
            except Empty:
                if not stopping.is_set():
                    self.check_timeouts()
                continue
            try:
                if isinstance(event, Shutdown) or stopping.is_set():
                    return
                self.handle_event(event)
                if not stopping.is_set():
                    self.check_timeouts()
            finally:
                events.task_done()
