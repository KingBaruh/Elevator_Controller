"""Choose routes without moving hardware or changing pending requests.

Start with plan_next_route() for a new trip, or propose_route_update() for an
existing one. The controller decides when a proposal can actually be applied.
"""

from dataclasses import replace

from .models import (
    CabinRequest,
    Direction,
    DoorState,
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

    def plan_next_route(
        self, elevator: Elevator, pending: PendingRequests, *, now: float
    ) -> Route | None:
        """Call only when there is no active route or service commitment.

        An overdue hall call leads. Otherwise finish the current service
        direction before choosing a new direction. Planning consumes no requests.
        """
        self._validate_inputs(elevator, pending, now)
        if not elevator.ready_to_depart or not pending:
            return None

        # Aging can override direction at a route boundary. A cabin request at
        # this floor can be served immediately without starting another trip.
        overdue = self._overdue_calls(pending, now)
        continuation = None
        cabin_here = any(request.floor == elevator.current_floor for request in pending.cabin)
        if not overdue and not cabin_here and elevator.last_direction is not None:
            continuation = self._sweep_terminal(elevator.current_floor, elevator.last_direction, pending)
        pickup = None
        if overdue:
            pickup = self._oldest(overdue)
        elif continuation is None and not pending.cabin:
            pickup = self._oldest(pending.hall)

        if pickup is not None:
            # Travel direction and pickup direction may differ: go UP to collect
            # a passenger who wants to go DOWN, then serve DOWN at the pickup.
            terminal_stop = PlannedStop(pickup.floor, pickup.direction)
        elif continuation is not None:
            terminal_stop = continuation
        else:
            terminal_stop = self._choose_cabin_stop(elevator, pending)

        direction = self._direction(elevator.current_floor, terminal_stop.floor)
        # Only an opposite-direction approach without cabin requests may extend.
        # Route.after_servicing() disables this permission after the first service.
        allow_extension = (
            pickup is not None
            and direction is not None
            and pickup.direction is not direction
            and not pending.cabin
            and not overdue
        )
        route = Route(
            origin_floor=elevator.current_floor,
            terminal_floor=terminal_stop.floor,
            travel_direction=direction,
            stops=(terminal_stop,),
            committed_pickup=pickup,
            allow_pickup_extension=allow_extension,
        )
        # Use the same rules to include intermediate stops in a new or active route.
        return self.propose_route_update(route, elevator, pending, now=now)

    def propose_route_update(
        self, route: Route, elevator: Elevator, pending: PendingRequests, *, now: float
    ) -> Route:
        """Propose stops without changing the active route or consuming requests.

        If the next target changes while moving, the controller must obtain
        hardware approval. Rejection leaves the original route in use.
        """
        self._validate_inputs(elevator, pending, now)
        if elevator.faulted or route.travel_direction is None:
            return route

        route = self._extend_pickup_approach(route, elevator, pending, now)
        route = self._extend_service_sweep(route, elevator, pending, now)
        direction = route.travel_direction
        stops_by_floor = {stop.floor: stop for stop in route.stops}

        for request in pending.cabin:
            if self._fits_route(request.floor, route, elevator):
                stops_by_floor.setdefault(request.floor, PlannedStop(request.floor, direction))

        for request in pending.hall:
            if request.direction is direction and self._fits_route(request.floor, route, elevator):
                # setdefault keeps an existing stop's service direction, including
                # a terminal pickup that serves the opposite direction to travel.
                stops_by_floor.setdefault(request.floor, PlannedStop(request.floor, direction))

        # Visit low-to-high floors going UP, and high-to-low going DOWN.
        ordered_floors = sorted(stops_by_floor, reverse=direction is Direction.DOWN)
        ordered_stops = tuple(stops_by_floor[floor] for floor in ordered_floors)
        return replace(route, stops=ordered_stops)

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

    def _sweep_terminal(
        self, origin: int, direction: Direction, pending: PendingRequests
    ) -> PlannedStop | None:
        """Find the last requested floor ahead, not the end of the building.

        Opposite-direction hall calls are collected only at the turnaround;
        the normal update loop skips them at intermediate floors.
        """
        floors = [
            request.floor for request in (*pending.cabin, *pending.hall)
            if self._is_ahead(origin, request.floor, direction)
        ]
        if not floors:
            return None
        terminal = max(floors) if direction is Direction.UP else min(floors)
        hall_at_terminal = [request for request in pending.hall if request.floor == terminal]
        service_direction = direction
        if hall_at_terminal and not any(request.direction is direction for request in hall_at_terminal):
            service_direction = hall_at_terminal[0].direction
        return PlannedStop(terminal, service_direction)

    def _extend_service_sweep(
        self, route: Route, elevator: Elevator, pending: PendingRequests, now: float
    ) -> Route:
        """Finish serving this direction before handling destinations behind us."""
        direction = route.travel_direction
        pickup = route.committed_pickup
        if direction is None or self._overdue_calls(pending, now):
            return route
        # An explicit pickup approached from the opposite direction keeps its
        # promise. Its separate, pre-service extension rule is handled above.
        if pickup is not None and pickup.direction is not direction:
            return route
        previous_terminal = route.stops[-1]
        turns_at_terminal = previous_terminal.service_direction is not direction
        if (turns_at_terminal and elevator.current_floor == route.terminal_floor
                and elevator.doors is not DoorState.CLOSED):
            return route  # The turnaround pickup has already started.

        new_terminal = self._sweep_terminal(route.terminal_floor, direction, pending)
        if new_terminal is None:
            return route
        stops = list(route.stops)
        if turns_at_terminal:
            # Moving the turnaround must not collect a DOWN caller mid-ascent
            # (or an UP caller mid-descent). Keep any matching service there.
            stops.pop()
            cabin_there = any(r.floor == route.terminal_floor for r in pending.cabin)
            hall_there = any(r.floor == route.terminal_floor and r.direction is direction for r in pending.hall)
            if cabin_there or hall_there:
                stops.append(PlannedStop(route.terminal_floor, direction))
        stops.append(new_terminal)
        return replace(route, terminal_floor=new_terminal.floor, stops=tuple(stops))

    def _extend_pickup_approach(
        self, route: Route, elevator: Elevator, pending: PendingRequests, now: float
    ) -> Route:
        """Move an opposite-direction pickup to the farthest matching hall call.

        Example: from 7, approach 3 UP, then receive 1 UP. Continue to 1 before
        collecting 3 on the way back UP, if no service has started yet.
        """
        pickup = route.committed_pickup
        direction = route.travel_direction
        # Keep the endpoint once service starts, or when cabin requests need priority.
        if not route.allow_pickup_extension or pending.cabin or elevator.doors is not DoorState.CLOSED:
            return route
        if pickup is None or direction is None or pickup.direction is direction:
            return route
        # Do not postpone any call further after it reaches the waiting threshold.
        if self._overdue_calls(pending, now):
            return route
        farther_calls = tuple(
            request for request in pending.hall
            if request.direction is pickup.direction
            and self._is_ahead(route.terminal_floor, request.floor, direction)
        )
        if not farther_calls:
            return route
        if direction is Direction.UP:
            farther = max(farther_calls, key=lambda request: request.floor)
        else:
            farther = min(farther_calls, key=lambda request: request.floor)
        # The old terminal pickup will be served in its requested direction on
        # the return trip. It is not fulfilled here and retains its wait time.
        stops = tuple(stop for stop in route.stops if stop.floor != route.terminal_floor)
        return replace(
            route, terminal_floor=farther.floor, committed_pickup=farther,
            stops=stops + (PlannedStop(farther.floor, farther.direction),),
        )

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

    def _overdue_calls(self, pending: PendingRequests, now: float) -> tuple[HallRequest, ...]:
        """Return hall calls that have waited longer than the threshold."""
        return tuple(
            request for request in pending.hall
            if now - request.created_at >= self.max_hall_wait
        )

    @staticmethod
    def _validate_inputs(elevator: Elevator, pending: PendingRequests, now: float) -> None:
        validate_time(now)
        if elevator.current_floor > pending.number_of_floors:
            raise ValueError("The elevator is outside the building.")
        if elevator.target_floor is not None and elevator.target_floor > pending.number_of_floors:
            raise ValueError("The target is outside the building.")
        if any(request.created_at > now for request in pending.hall):
            raise ValueError("Request timestamps and now must use the same clock.")
