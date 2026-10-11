"""Estimate time to matching door service by dry-running the scheduling policy.

Estimates use known requests and the last reported position/door state. They
include existing commitments and in-flight assignments, but cannot predict
future passengers or hardware rejection. Nothing here commands real hardware.
"""

from dataclasses import replace
from math import inf

from elevator_controller import CabinRequest, DoorState, MotionState, PendingRequests


class DispatchPolicy:
    def __init__(self, number_of_floors, *, seconds_per_floor=0.7, door_seconds=0.45,
                 dwell_seconds=2.0):
        self.number_of_floors = number_of_floors
        self.seconds_per_floor = seconds_per_floor
        self.door_seconds = door_seconds
        self.dwell_seconds = dwell_seconds

    def choose(self, request, statuses, assignments, pending_cabins, policies, *, now):
        candidates = []
        for status in statuses:
            if status.controller.fault:
                continue
            car_id = status.elevator_id
            reserved = [call.request for call in assignments if call.elevator_id == car_id]
            eta = self.estimate(request, status, reserved, pending_cabins.get(car_id, ()),
                                policies[car_id], now=now)
            if eta != inf:
                candidates.append((eta, len(reserved), car_id))
        return min(candidates)[2] if candidates else None

    def estimate(self, request, status, reserved, cabins, policy, *, now):
        state = status.controller
        if state.fault:
            return inf
        pending = PendingRequests(self.number_of_floors)
        for call in (*state.hall_requests, *reserved, request):
            pending.add_hall(call)
        for call in state.cabin_requests:
            pending.add_cabin(call)
        for floor in cabins:
            pending.add_cabin(CabinRequest(floor))
        elevator, route = state.elevator, state.route
        elapsed = 0.0
        position = status.position

        def matching(stop):
            return stop is not None and stop.floor == request.floor and stop.service_direction is request.direction

        # Complete an existing door cycle before planning new movement. OPEN
        # snapshots have already consumed their stop; OPENING snapshots have not.
        if elevator.doors in (DoorState.OPEN, DoorState.OPENING):
            opening = elevator.doors is DoorState.OPENING
            elapsed += (1 - status.door_open_fraction) * self.door_seconds if opening else 0
            if matching(state.service_stop):
                return elapsed
            stop = state.service_stop
            if stop is not None:
                pending.mark_served(stop.floor, stop.service_direction)
                if opening and route is not None:
                    route = route.after_servicing(stop.floor)
                elevator = replace(elevator, last_direction=stop.service_direction or elevator.last_direction)
            dwell = self.dwell_seconds if opening else max(0, (status.dwell_deadline or now) - now)
            elapsed += dwell + self.door_seconds
            elevator = replace(elevator, doors=DoorState.CLOSED)
        elif elevator.doors is DoorState.CLOSING:
            elapsed += status.door_open_fraction * self.door_seconds
            elevator = replace(elevator, doors=DoorState.CLOSED)

        # A finite building/request set normally needs far fewer iterations.
        # Bound the forecast so a custom non-serving policy cannot hang dispatch.
        for _ in range(4 * (len(pending.hall) + len(pending.cabin)) + 20):
            simulated_now = now + elapsed
            if route is None:
                route = policy.plan_next_route(elevator, pending, now=simulated_now)
            else:
                proposal = policy.propose_route_update(route, elevator, pending, now=simulated_now)
                feasible = True
                if elevator.motion is MotionState.MOVING and proposal.next_stop.floor != route.next_stop.floor:
                    direction = route.travel_direction.step
                    distance = (proposal.next_stop.floor - position) * direction
                    remaining = (route.next_stop.floor - position) * direction
                    feasible = distance >= 0.25 and not (distance > remaining and remaining < 0.25)
                if feasible:
                    route = proposal
            if route is None:
                return inf
            stop = route.next_stop
            elapsed += abs(stop.floor - position) * self.seconds_per_floor + self.door_seconds
            if matching(stop):
                return elapsed
            pending.mark_served(stop.floor, stop.service_direction)
            position = float(stop.floor)
            elevator = replace(elevator, current_floor=stop.floor, motion=MotionState.STOPPED,
                               target_floor=None, last_direction=stop.service_direction or elevator.last_direction)
            route = route.after_servicing(stop.floor)
            elapsed += self.dwell_seconds + self.door_seconds
        return inf
