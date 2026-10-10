"""Shared policy contract and helpers, without a default scheduling strategy."""

from abc import ABC, abstractmethod

from .models import (
    CabinRequest, Direction, Elevator, HallRequest, MotionState, PlannedStop,
    Route, validate_time,
)
from .requests import PendingRequests


class ElevatorPolicy(ABC):
    """Choose routes without issuing commands or mutating pending requests.

    Each concrete policy defines both scheduling decisions. Shared helpers
    below provide validation, geometry and the cabin tie-breaking rule used
    by both policies; they do not impose priority, sweeps or hall-call aging.
    """

    @abstractmethod
    def plan_next_route(
        self, elevator: Elevator, pending: PendingRequests, *, now: float
    ) -> Route | None:
        """Choose a new route when no route or service commitment is active."""
        raise NotImplementedError

    @abstractmethod
    def propose_route_update(
        self, route: Route, elevator: Elevator, pending: PendingRequests, *, now: float
    ) -> Route:
        """Propose an update; the controller obtains hardware approval if needed."""
        raise NotImplementedError

    def _choose_cabin_stop(self, elevator: Elevator, pending: PendingRequests) -> PlannedStop:
        """The nearest cabin destination sets the direction; the farthest sets its end."""
        current = elevator.current_floor

        def cabin_priority(request: CabinRequest) -> tuple[int, int, int]:
            distance = abs(request.floor - current)
            keeps_direction = self._direction(current, request.floor) == elevator.last_direction
            # Compare distance first, then prefer the previous direction, then
            # the lower floor. min() compares these tuple fields left to right.
            direction_priority = 0 if keeps_direction else 1
            return distance, direction_priority, request.floor

        closest = min(pending.cabin, key=cabin_priority)
        direction = self._direction(current, closest.floor)
        if direction is None:
            # Already at the cabin destination: share service with the oldest
            # local hall call, if one exists, without starting a movement.
            local_calls = tuple(request for request in pending.hall if request.floor == current)
            service_direction = self._oldest(local_calls).direction if local_calls else None
            return PlannedStop(current, service_direction)

        forward_floors = [
            request.floor for request in pending.cabin
            if self._is_ahead(current, request.floor, direction)
        ]
        terminal = max(forward_floors) if direction is Direction.UP else min(forward_floors)
        return PlannedStop(terminal, direction)

    @staticmethod
    def _direction(origin: int, destination: int) -> Direction | None:
        """Return the direction from origin to destination, or None if equal."""
        if destination == origin:
            return None
        return Direction.UP if destination > origin else Direction.DOWN

    @staticmethod
    def _is_ahead(origin: int, destination: int, direction: Direction) -> bool:
        """Strictly ahead: the origin itself does not count."""
        if direction is Direction.UP:
            return destination > origin
        return destination < origin

    @staticmethod
    def _fits_route(floor: int, route: Route, elevator: Elevator) -> bool:
        """Return True if the floor is between the current and terminal floors."""
        # While moving, current_floor is the last reported floor; it is too late
        # to add a stop there. When stopped, service at that floor is allowed.
        current = elevator.current_floor
        if elevator.motion is MotionState.MOVING and floor == current:
            return False
        if route.travel_direction is Direction.UP:
            return current <= floor <= route.terminal_floor
        return route.terminal_floor <= floor <= current

    @staticmethod
    def _oldest(requests: tuple[HallRequest, ...]) -> HallRequest:
        """Return the oldest request, breaking ties by floor and direction."""
        # Equal timestamps: choose the lower floor, then direction alphabetically.
        return min(requests, key=lambda request: (request.created_at, request.floor, request.direction.value))

    @staticmethod
    def _validate_inputs(elevator: Elevator, pending: PendingRequests, now: float) -> None:
        validate_time(now)
        if elevator.current_floor > pending.number_of_floors:
            raise ValueError("The elevator is outside the building.")
        if elevator.target_floor is not None and elevator.target_floor > pending.number_of_floors:
            raise ValueError("The target is outside the building.")
        if any(request.created_at > now for request in pending.hall):
            raise ValueError("Request timestamps and now must use the same clock.")
