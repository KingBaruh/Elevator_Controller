"""Deterministic scheduling, independent of threads, I/O and wall-clock waits."""

from .models import (
    Direction,
    Elevator,
    HallRequest,
    MotionState,
    PlannedStop,
    Route,
    validate_time,
)
from .requests import PendingRequests


class SchedulingPolicy:
    def __init__(self, max_hall_wait: float = 60.0) -> None:
        """60 seconds is a configurable example, not a hardware requirement."""
        validate_time(max_hall_wait)
        if max_hall_wait <= 0:
            raise ValueError("The hall wait threshold must be positive.")
        self.max_hall_wait = max_hall_wait

    @staticmethod
    def _direction(origin: int, destination: int) -> Direction | None:
        if destination == origin:
            return None
        return Direction.UP if destination > origin else Direction.DOWN

    @staticmethod
    def _oldest(requests: tuple[HallRequest, ...]) -> HallRequest:
        # Equal timestamps have a stable, documented floor/direction tie-break.
        return min(requests, key=lambda r: (r.created_at, r.floor, r.direction.value))

    @staticmethod
    def _check_building(elevator: Elevator, pending: PendingRequests) -> None:
        if elevator.current_floor > pending.number_of_floors:
            raise ValueError("The elevator is outside the building.")
        if elevator.target_floor is not None and elevator.target_floor > pending.number_of_floors:
            raise ValueError("The target is outside the building.")

    def plan_next_route(
        self, elevator: Elevator, pending: PendingRequests, *, now: float
    ) -> Route | None:
        """Call only when there is no active route or service commitment.

        An overdue hall call leads; otherwise cabin requests lead; otherwise
        the oldest hall call leads. Planning never consumes pending requests.
        """
        validate_time(now)
        self._check_building(elevator, pending)
        if any(request.created_at > now for request in pending.hall):
            raise ValueError("Request timestamps and now must use the same clock.")
        if not elevator.ready_to_depart or not pending:
            return None

        current = elevator.current_floor
        overdue = tuple(r for r in pending.hall if now - r.created_at >= self.max_hall_wait)
        pickup = None
        if overdue:
            pickup = self._oldest(overdue)
        elif not pending.cabin:
            pickup = self._oldest(pending.hall)

        if pickup is not None:
            terminal = pickup.floor
            direction = self._direction(current, terminal)
            terminal_service = pickup.direction
        else:
            closest = min(
                pending.cabin,
                key=lambda r: (
                    abs(r.floor - current),
                    0 if self._direction(current, r.floor) == elevator.last_direction else 1,
                    r.floor,
                ),
            )
            direction = self._direction(current, closest.floor)
            if direction is None:
                terminal = current
                local_calls = tuple(r for r in pending.hall if r.floor == current)
                terminal_service = self._oldest(local_calls).direction if local_calls else None
            else:
                forward = [r.floor for r in pending.cabin if (r.floor - current) * direction.step > 0]
                terminal = max(forward) if direction is Direction.UP else min(forward)
                terminal_service = direction

        route = Route(
            origin_floor=current,
            terminal_floor=terminal,
            travel_direction=direction,
            stops=(PlannedStop(terminal, terminal_service),),
            committed_pickup=pickup,
        )
        return self.propose_route_update(route, elevator, pending)

    def propose_route_update(
        self, route: Route, elevator: Elevator, pending: PendingRequests
    ) -> Route:
        """Return an immutable proposal without changing the active route.

        When the next target changes during movement, the controller must get
        hardware approval before adopting the proposal. On rejection, retain
        the old route and all pending requests. No stop is ever removed here.
        """
        self._check_building(elevator, pending)
        if elevator.faulted or route.travel_direction is None:
            return route

        direction = route.travel_direction
        current = elevator.current_floor
        candidates = {stop.floor: stop for stop in route.stops}

        def fits(floor: int) -> bool:
            distance = (floor - current) * direction.step
            ahead = distance > 0 if elevator.motion is MotionState.MOVING else distance >= 0
            return ahead and (route.terminal_floor - floor) * direction.step >= 0

        for request in pending.cabin:
            if fits(request.floor):
                candidates.setdefault(request.floor, PlannedStop(request.floor, direction))
        for request in pending.hall:
            if request.direction is direction and fits(request.floor):
                # Preserve the service direction of a committed terminal pickup.
                candidates.setdefault(request.floor, PlannedStop(request.floor, direction))

        ordered = tuple(candidates[floor] for floor in sorted(candidates, reverse=direction is Direction.DOWN))
        return Route(
            origin_floor=route.origin_floor,
            terminal_floor=route.terminal_floor,
            travel_direction=direction,
            stops=ordered,
            committed_pickup=route.committed_pickup,
        )
