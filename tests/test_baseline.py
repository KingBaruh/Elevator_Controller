"""Behavior of the strict-priority baseline, using the same controller as production."""

import unittest

from elevator_controller import BaselinePolicy, CabinRequest, Direction, Elevator, HallRequest, MotionState, PendingRequests
from elevator_controller.models import DoorState


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.policy = BaselinePolicy()
        self.pending = PendingRequests(10)

    def test_internal_five_beats_external_eight_even_when_call_is_overdue(self):
        self.pending.add_hall(HallRequest(8, Direction.UP, 0))
        self.pending.add_cabin(CabinRequest(5))
        route = self.policy.plan_next_route(Elevator(7, last_direction=Direction.UP), self.pending, now=100)
        self.assertEqual(route.next_stop.floor, 5)
        self.assertEqual(route.travel_direction, Direction.DOWN)
        self.assertEqual(len(self.pending.hall), 1)

    def test_internal_priority_does_not_skip_a_matching_hall_stop_on_the_way(self):
        self.pending.add_cabin(CabinRequest(7))
        self.pending.add_hall(HallRequest(3, Direction.UP, 0))
        self.pending.add_hall(HallRequest(2, Direction.DOWN, 0))
        route = self.policy.plan_next_route(Elevator(1), self.pending, now=1)
        self.assertEqual([stop.floor for stop in route.stops], [3])

    def test_reverse_request_does_not_reverse_a_moving_elevator(self):
        self.pending.add_cabin(CabinRequest(7))
        route = self.policy.plan_next_route(Elevator(1), self.pending, now=0)
        self.pending.add_cabin(CabinRequest(1))
        proposal = self.policy.propose_route_update(route, Elevator(4, motion=MotionState.MOVING), self.pending, now=1)
        self.assertEqual(proposal.next_stop.floor, 7)

    def test_doors_opening_preserve_the_stop_being_serviced(self):
        self.pending.add_cabin(CabinRequest(7))
        route = self.policy.plan_next_route(Elevator(1), self.pending, now=0)
        self.pending.add_cabin(CabinRequest(5))
        proposal = self.policy.propose_route_update(route, Elevator(7, doors=DoorState.OPENING), self.pending, now=1)
        self.assertEqual(proposal, route)
