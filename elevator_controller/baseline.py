"""A strict internal-priority interpretation of the original exercise."""

from dataclasses import replace

from .models import DoorState, Elevator, MotionState, PlannedStop, Route
from .requests import PendingRequests
from .scheduler import SchedulingPolicy


class BaselinePolicy(SchedulingPolicy):
    """Reconsider priority at each stop, with no hall-call aging or service sweep.

    Reuses geometry, validation and cabin tie-breaking from SchedulingPolicy.
    The two public methods below define different scheduling decisions. Keeping
    only the next stop lets the unchanged controller replan after doors close.
    Hardware approval is still required to change a target while moving.
    """

    def plan_next_route(
        self, elevator: Elevator, pending: PendingRequests, *, now: float
    ) -> Route | None:
        self._validate_inputs(elevator, pending, now)
        if not elevator.ready_to_depart or not pending:
            return None

        pickup = None
        if pending.cabin:
            # Internal destinations always lead, even if an external call is old.
            terminal = self._choose_cabin_stop(elevator, pending)
        else:
            pickup = self._oldest(pending.hall)
            terminal = PlannedStop(pickup.floor, pickup.direction)
        route = Route(
            origin_floor=elevator.current_floor,
            terminal_floor=terminal.floor,
            travel_direction=self._direction(elevator.current_floor, terminal.floor),
            stops=(terminal,),
            committed_pickup=pickup,
        )
        return self._next_stop_on_way(route, elevator, pending)

    def propose_route_update(
        self, route: Route, elevator: Elevator, pending: PendingRequests, *, now: float
    ) -> Route:
        self._validate_inputs(elevator, pending, now)
        if elevator.faulted:
            return route
        if elevator.motion is MotionState.MOVING:
            # Do not reverse between floors; only insert an achievable stop ahead.
            return self._next_stop_on_way(route, elevator, pending)
        if elevator.doors is not DoorState.CLOSED:
            return route  # Finish the current door/service operation first.
        return self.plan_next_route(elevator, pending, now=now) or route

    def _next_stop_on_way(
        self, route: Route, elevator: Elevator, pending: PendingRequests
    ) -> Route:
        direction = route.travel_direction
        if direction is None:
            return route
        stops = {route.next_stop.floor: route.next_stop}
        for request in pending.cabin:
            if self._fits_route(request.floor, route, elevator):
                stops.setdefault(request.floor, PlannedStop(request.floor, direction))
        for request in pending.hall:
            if request.direction is direction and self._fits_route(request.floor, route, elevator):
                stops.setdefault(request.floor, PlannedStop(request.floor, direction))
        next_floor = min(stops, key=lambda floor: abs(floor - elevator.current_floor))
        pickup = route.committed_pickup
        if pickup is not None and pickup.floor != next_floor:
            pickup = None
        # Other requests remain pending. After this stop, internal priority is
        # considered again instead of retaining a promise to the old endpoint.
        return replace(
            route, terminal_floor=next_floor, stops=(stops[next_floor],),
            committed_pickup=pickup, allow_pickup_extension=False,
        )
