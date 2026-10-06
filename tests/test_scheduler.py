import unittest
from dataclasses import replace

from elevator_controller import (
    CabinRequest,
    Direction,
    DoorState,
    Elevator,
    HallRequest,
    MotionState,
    PendingRequests,
    SchedulingPolicy,
)


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.pending = PendingRequests(10)
        self.policy = SchedulingPolicy(max_hall_wait=60)

    def hall(self, floor, direction=Direction.UP, created_at=0):
        request = HallRequest(floor, direction, created_at)
        self.pending.add_hall(request)
        return request

    def cabin(self, floor):
        self.pending.add_cabin(CabinRequest(floor))

    def plan(self, floor=1, now=10, **state):
        return self.policy.plan_next_route(Elevator(floor, **state), self.pending, now=now)

    def floors(self, route):
        return [stop.floor for stop in route.stops]

    def test_empty_building_stays_put(self):
        self.assertIsNone(self.plan())

    def test_cabin_priority_over_a_closer_regular_hall_call(self):
        self.cabin(8)
        self.hall(4, Direction.DOWN)
        route = self.plan(floor=5)
        self.assertEqual(self.floors(route), [8])
        self.assertEqual(route.travel_direction, Direction.UP)

    def test_same_direction_pickup_and_opposite_direction_deferral(self):
        self.cabin(7)
        self.hall(3, Direction.UP)
        self.hall(4, Direction.DOWN)
        route = self.plan()
        self.assertEqual(self.floors(route), [3, 7])
        self.assertEqual(len(self.pending.hall), 2)  # Planning does not serve anyone.

    def test_downward_route_orders_stops_and_only_picks_up_down_calls(self):
        self.cabin(2)
        self.cabin(6)
        self.hall(5, Direction.DOWN)
        self.hall(4, Direction.UP)
        route = self.plan(floor=9)
        self.assertEqual(self.floors(route), [6, 5, 2])

    def test_nearest_cabin_sets_direction_then_all_known_forward_cabins_join(self):
        for floor in (2, 6, 9):
            self.cabin(floor)
        route = self.plan(floor=5)
        self.assertEqual(self.floors(route), [6, 9])
        self.assertEqual(route.terminal_floor, 9)

    def test_equal_distance_prefers_previous_direction(self):
        self.cabin(3)
        self.cabin(7)
        route = self.plan(floor=5, last_direction=Direction.UP)
        self.assertEqual(self.floors(route), [7])

    def test_equal_distance_without_previous_direction_prefers_lower_floor(self):
        self.cabin(3)
        self.cabin(7)
        self.assertEqual(self.floors(self.plan(floor=5)), [3])

    def test_without_cabins_oldest_hall_call_leads(self):
        old = self.hall(8, Direction.DOWN, created_at=0)
        self.hall(2, Direction.UP, created_at=5)
        route = self.plan()
        self.assertEqual(route.committed_pickup, old)
        self.assertEqual(self.floors(route), [2, 8])
        self.assertEqual(route.stops[-1].service_direction, Direction.DOWN)

    def test_new_cabin_request_does_not_cancel_committed_eighth_floor_pickup(self):
        pickup = self.hall(8, Direction.UP)
        self.cabin(7)
        # The pickup was already committed before the internal request arrived.
        original_requests = PendingRequests(10)
        original_requests.add_hall(pickup)
        route = self.policy.plan_next_route(Elevator(1), original_requests, now=10)
        route = self.policy.propose_route_update(route, Elevator(1), self.pending, now=10)
        self.assertEqual(self.floors(route), [7, 8])

        self.pending.mark_served(7, route.next_stop.service_direction)
        route = route.after_servicing(7)
        self.cabin(1)
        route = self.policy.propose_route_update(route, Elevator(7), self.pending, now=10)
        self.assertEqual(self.floors(route), [8])

        self.pending.mark_served(8, route.next_stop.service_direction)
        self.assertIsNone(route.after_servicing(8))
        self.assertEqual(self.floors(self.plan(floor=8, now=20)), [1])

    def test_new_request_cannot_extend_sweep_endpoint(self):
        self.cabin(7)
        route = self.plan()
        self.cabin(9)
        self.hall(8)
        proposal = self.policy.propose_route_update(route, Elevator(2, motion=MotionState.MOVING), self.pending, now=10)
        self.assertEqual(self.floors(proposal), [7])
        self.assertEqual(proposal.terminal_floor, 7)
        self.assertEqual({r.floor for r in self.pending.cabin}, {7, 9})

    def test_new_internal_intermediate_stop_can_join(self):
        self.cabin(7)
        route = self.plan()
        self.cabin(4)
        proposal = self.policy.propose_route_update(route, Elevator(2, motion=MotionState.MOVING), self.pending, now=10)
        self.assertEqual(self.floors(proposal), [4, 7])

    def test_rejected_retarget_leaves_original_route_and_request_intact(self):
        self.cabin(7)
        active_route = self.plan()
        request = self.hall(3)
        proposal = self.policy.propose_route_update(
            active_route, Elevator(2, motion=MotionState.MOVING), self.pending, now=10
        )
        self.assertEqual(self.floors(proposal), [3, 7])
        # Simulate rejection: the caller deliberately does not adopt the proposal.
        self.assertEqual(self.floors(active_route), [7])
        self.assertIn(request, self.pending.hall)
        after_passing = self.policy.propose_route_update(
            active_route, Elevator(3, motion=MotionState.MOVING), self.pending, now=10
        )
        self.assertEqual(self.floors(after_passing), [7])

    def test_overdue_call_overrides_cabin_priority_at_route_boundary(self):
        old = self.hall(2, Direction.UP, created_at=0)
        self.cabin(9)
        before_threshold = self.plan(floor=5, now=59)
        self.assertEqual(before_threshold.terminal_floor, 9)
        at_threshold = self.plan(floor=5, now=60)
        self.assertEqual(at_threshold.committed_pickup, old)
        self.assertEqual(at_threshold.terminal_floor, 2)

    def test_oldest_overdue_call_wins_and_internal_stops_on_way_are_served(self):
        self.hall(3, Direction.UP, created_at=10)
        oldest = self.hall(2, Direction.UP, created_at=0)
        self.cabin(4)
        self.cabin(9)
        route = self.plan(floor=6, now=100)
        self.assertEqual(route.committed_pickup, oldest)
        self.assertEqual(self.floors(route), [4, 2])

    def test_hall_call_at_current_floor_needs_no_travel(self):
        self.hall(5, Direction.DOWN)
        route = self.plan(floor=5)
        self.assertEqual(self.floors(route), [5])
        self.assertIsNone(route.travel_direction)
        self.assertEqual(route.next_stop.service_direction, Direction.DOWN)

    def test_cabin_call_at_current_floor_needs_no_travel(self):
        self.cabin(5)
        route = self.plan(floor=5)
        self.assertIsNone(route.travel_direction)
        self.assertIsNone(route.next_stop.service_direction)

    def test_current_floor_cabin_can_share_service_with_oldest_local_hall_call(self):
        self.cabin(5)
        self.hall(5, Direction.UP, created_at=5)
        self.hall(5, Direction.DOWN, created_at=0)
        route = self.plan(floor=5)
        self.assertEqual(route.next_stop.service_direction, Direction.DOWN)

    def test_empty_opposite_direction_pickup_extends_in_both_directions(self):
        for origin, first, farther, service in (
            (7, 3, 1, Direction.UP),
            (3, 7, 10, Direction.DOWN),
        ):
            with self.subTest(origin=origin):
                pending = PendingRequests(10)
                original_call = HallRequest(first, service, 0)
                pending.add_hall(original_call)
                route = self.policy.plan_next_route(Elevator(origin), pending, now=10)
                pending.add_hall(HallRequest(farther, service, 11))
                proposal = self.policy.propose_route_update(
                    route, Elevator(origin, motion=MotionState.MOVING), pending, now=11
                )
                self.assertEqual(self.floors(proposal), [farther])
                self.assertEqual(proposal.terminal_floor, farther)
                self.assertEqual(proposal.next_stop.service_direction, service)
                self.assertEqual(proposal.travel_direction, route.travel_direction)
                self.assertIn(original_call, pending.hall)
                self.assertEqual(self.floors(route), [first])  # Proposal is not yet adopted.

    def test_initial_plan_uses_farthest_matching_pickup_when_calls_are_already_known(self):
        self.hall(3, Direction.UP, created_at=0)
        self.hall(2, Direction.UP, created_at=1)
        self.hall(1, Direction.UP, created_at=2)
        self.assertEqual(self.floors(self.plan(floor=7)), [1])

    def test_new_cabin_request_blocks_pickup_extension(self):
        self.hall(3, Direction.UP)
        route = self.plan(floor=7)
        self.hall(1, Direction.UP, created_at=11)
        self.cabin(6)
        proposal = self.policy.propose_route_update(route, Elevator(7), self.pending, now=11)
        self.assertEqual(self.floors(proposal), [6, 3])
        self.assertEqual(proposal.terminal_floor, 3)

    def test_different_hall_direction_does_not_extend_pickup(self):
        self.hall(3, Direction.UP)
        route = self.plan(floor=7)
        self.hall(2, Direction.DOWN, created_at=11)
        proposal = self.policy.propose_route_update(route, Elevator(7), self.pending, now=11)
        self.assertEqual(self.floors(proposal), [3])

    def test_same_direction_approach_keeps_its_original_endpoint(self):
        self.hall(3, Direction.UP)
        route = self.plan(floor=1)
        self.hall(7, Direction.UP, created_at=11)
        proposal = self.policy.propose_route_update(route, Elevator(1), self.pending, now=11)
        self.assertEqual(self.floors(proposal), [3])

    def test_doors_opening_open_or_closing_block_pickup_extension(self):
        self.hall(3, Direction.UP)
        route = self.plan(floor=7)
        self.hall(1, Direction.UP, created_at=11)
        for doors in (DoorState.OPENING, DoorState.OPEN, DoorState.CLOSING):
            with self.subTest(doors=doors):
                proposal = self.policy.propose_route_update(
                    route, Elevator(3, doors=doors), self.pending, now=11
                )
                self.assertEqual(self.floors(proposal), [3])

    def test_first_intermediate_service_permanently_ends_pickup_extension_phase(self):
        self.hall(3, Direction.UP)
        self.hall(5, Direction.DOWN, created_at=1)
        route = self.plan(floor=7)
        self.assertEqual(self.floors(route), [5, 3])
        self.pending.mark_served(5, Direction.DOWN)
        route = route.after_servicing(5)
        self.hall(1, Direction.UP, created_at=11)
        proposal = self.policy.propose_route_update(route, Elevator(5), self.pending, now=11)
        self.assertEqual(self.floors(proposal), [3])
        self.assertFalse(proposal.allow_pickup_extension)

    def test_extension_preserves_other_committed_stops(self):
        self.hall(3, Direction.UP)
        self.hall(5, Direction.DOWN, created_at=1)
        route = self.plan(floor=7)
        self.hall(1, Direction.UP, created_at=11)
        proposal = self.policy.propose_route_update(route, Elevator(7), self.pending, now=11)
        self.assertEqual(self.floors(proposal), [5, 1])
        self.assertEqual(proposal.next_stop.service_direction, Direction.DOWN)
        self.assertEqual({r.floor for r in self.pending.hall}, {1, 3, 5})

    def test_overdue_displaced_call_blocks_further_extensions(self):
        original = self.hall(5, Direction.UP, created_at=0)
        route = self.plan(floor=8)
        self.hall(3, Direction.UP, created_at=11)
        route = self.policy.propose_route_update(route, Elevator(8), self.pending, now=11)
        self.assertEqual(route.terminal_floor, 3)
        self.hall(1, Direction.UP, created_at=60)
        proposal = self.policy.propose_route_update(route, Elevator(7), self.pending, now=60)
        self.assertEqual(proposal.terminal_floor, 3)
        self.assertIn(original, self.pending.hall)

    def test_overdue_pickup_is_not_displaced_even_in_initial_plan(self):
        self.hall(3, Direction.UP, created_at=0)
        self.hall(1, Direction.UP, created_at=59)
        route = self.plan(floor=7, now=60)
        self.assertEqual(self.floors(route), [3])
        self.assertFalse(route.allow_pickup_extension)

    def test_terminal_pickup_keeps_requested_direction_even_when_approached_oppositely(self):
        self.hall(8, Direction.DOWN)
        route = self.plan()
        self.hall(8, Direction.UP, created_at=5)
        self.cabin(8)
        proposal = self.policy.propose_route_update(route, Elevator(2), self.pending, now=10)
        self.assertEqual(proposal.travel_direction, Direction.UP)
        self.assertEqual(proposal.next_stop.service_direction, Direction.DOWN)
        self.pending.mark_served(8, proposal.next_stop.service_direction)
        self.assertEqual([(r.floor, r.direction) for r in self.pending.hall], [(8, Direction.UP)])
        self.assertFalse(self.pending.cabin)

    def test_do_not_dispatch_with_opening_open_or_closing_doors(self):
        self.cabin(7)
        for doors in (DoorState.OPENING, DoorState.OPEN, DoorState.CLOSING):
            with self.subTest(doors=doors):
                self.assertIsNone(self.plan(doors=doors))

    def test_do_not_start_new_route_while_moving_or_faulted(self):
        self.cabin(7)
        self.assertIsNone(self.plan(motion=MotionState.MOVING))
        self.assertIsNone(self.plan(faulted=True))

    def test_cannot_complete_a_stop_out_of_order(self):
        self.cabin(3)
        self.cabin(7)
        route = self.plan()
        with self.assertRaises(ValueError):
            route.after_servicing(7)

    def test_completed_stop_is_not_reintroduced_after_requests_are_served(self):
        self.cabin(3)
        self.cabin(7)
        route = self.plan()
        self.pending.mark_served(3, route.next_stop.service_direction)
        route = route.after_servicing(3)
        route = self.policy.propose_route_update(route, Elevator(3), self.pending, now=10)
        self.assertEqual(self.floors(route), [7])

    def test_future_hall_timestamp_is_rejected(self):
        self.hall(3, created_at=20)
        with self.assertRaises(ValueError):
            self.plan(now=10)

    def test_out_of_building_state_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(floor=11)

    def test_endpoint_and_commitments_survive_all_possible_new_requests(self):
        # Sweep both directions across all possible new button inputs.
        for origin, terminal in ((1, 7), (10, 3)):
            requests = PendingRequests(10)
            requests.add_cabin(CabinRequest(terminal))
            route = self.policy.plan_next_route(Elevator(origin), requests, now=0)
            direction = route.travel_direction
            moving = Elevator(origin + direction.step, motion=MotionState.MOVING)
            for floor in range(1, 11):
                requests.add_cabin(CabinRequest(floor))
                if floor < 10:
                    requests.add_hall(HallRequest(floor, Direction.UP, 0))
                if floor > 1:
                    requests.add_hall(HallRequest(floor, Direction.DOWN, 0))
            proposal = self.policy.propose_route_update(route, moving, requests, now=0)
            with self.subTest(origin=origin):
                self.assertEqual(proposal.terminal_floor, terminal)
                self.assertEqual(proposal.travel_direction, direction)
                self.assertIn(route.next_stop, proposal.stops)
                for stop in proposal.stops:
                    self.assertGreater((stop.floor - moving.current_floor) * direction.step, 0)
                    self.assertGreaterEqual((terminal - stop.floor) * direction.step, 0)


class RequestTests(unittest.TestCase):
    def test_duplicate_hall_press_keeps_original_wait_time(self):
        pending = PendingRequests(10)
        first = HallRequest(3, Direction.UP, 10)
        self.assertTrue(pending.add_hall(first))
        self.assertFalse(pending.add_hall(replace(first, created_at=30)))
        self.assertEqual(pending.hall, (first,))

    def test_opposite_directions_are_distinct_and_cabins_are_deduplicated(self):
        pending = PendingRequests(10)
        pending.add_hall(HallRequest(3, Direction.UP, 0))
        pending.add_hall(HallRequest(3, Direction.DOWN, 0))
        self.assertTrue(pending.add_cabin(CabinRequest(3)))
        self.assertFalse(pending.add_cabin(CabinRequest(3)))
        pending.mark_served(3, Direction.UP)
        self.assertEqual(pending.hall, (HallRequest(3, Direction.DOWN, 0),))
        self.assertFalse(pending.cabin)

    def test_building_boundaries(self):
        pending = PendingRequests(10)
        for request in (
            HallRequest(1, Direction.DOWN, 0),
            HallRequest(10, Direction.UP, 0),
            HallRequest(11, Direction.DOWN, 0),
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                pending.add_hall(request)
        with self.assertRaises(ValueError):
            pending.add_cabin(CabinRequest(11))

    def test_invalid_data_is_rejected(self):
        for floor in (0, -1, 1.5, True):
            with self.subTest(floor=floor), self.assertRaises(ValueError):
                CabinRequest(floor)
        with self.assertRaises(ValueError):
            HallRequest(3, "up", 0)
        with self.assertRaises(ValueError):
            HallRequest(3, Direction.UP, float("nan"))
        with self.assertRaises(ValueError):
            Elevator(3, motion=MotionState.MOVING, doors=DoorState.OPEN)

    def test_wait_threshold_must_be_finite_and_positive(self):
        for threshold in (0, -1, float("inf"), float("nan")):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                SchedulingPolicy(threshold)


if __name__ == "__main__":
    unittest.main()
