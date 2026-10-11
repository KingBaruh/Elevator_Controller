import time
import unittest
from queue import Empty
from random import Random

from elevator_controller import BaselinePolicy, Direction, DoorState
from elevator_controller.events import HallButtonPressed

from elevator_group import ElevatorGroupSystem
from elevator_group.messages import HallCallServed, ReleaseHallCalls


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Harness:
    """Run the same message handlers/physics with virtual time, without threads."""

    def __init__(self, **options):
        self.clock = Clock()
        self.system = ElevatorGroupSystem(clock=self.clock, seconds_per_floor=1, door_seconds=0.5,
                                          dwell_seconds=1, **options)
        self.hardware = self.system.hardware
        self.dispatcher = self.system.dispatcher

    def forward_source(self):
        for message in self.hardware.drain_events():
            self.system.events.put(message)

    def drain_dispatcher(self):
        count = 0
        while True:
            try:
                message = self.system.events.get_nowait()
            except Empty:
                return count
            self.dispatcher.handle_event(message)
            self.system.events.task_done()
            count += 1

    def drain_cars(self):
        count = 0
        for car_id, events in self.system.car_queues.items():
            while True:
                try:
                    message = events.get_nowait()
                except Empty:
                    break
                self.system.controllers[car_id].handle_event(message)
                events.task_done()
                count += 1
        return count

    def pump(self):
        self.forward_source()
        for _ in range(100):
            messages = self.drain_dispatcher() + self.drain_cars()
            if not messages:
                return
        raise AssertionError("Group message cycle did not become quiescent.")

    def advance(self, seconds=0.1):
        self.clock.now = round(self.clock.now + seconds, 10)
        self.hardware.tick()
        self.pump()
        for controller in self.system.controllers.values():
            controller.check_timeouts()
        self.pump()
        self.dispatcher.check_timeouts()

    def until(self, predicate, seconds=60):
        for _ in range(round(seconds / 0.1)):
            if predicate():
                return
            self.advance()
        raise AssertionError(f"Scenario timed out: {self.system.snapshot}")


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.system.stop)

    def test_nearby_calls_use_both_cars_and_move_concurrently(self):
        self.h.hardware.press_hall(3, Direction.UP)
        self.h.hardware.press_hall(9, Direction.DOWN)
        self.h.pump()
        self.assertEqual({call.request.floor: call.elevator_id for call in self.h.system.snapshot.calls},
                         {3: "A", 9: "B"})
        self.h.advance(0.5)
        a, b = (self.h.hardware.cars[key].snapshot for key in ("A", "B"))
        self.assertGreater(a.position, 1)
        self.assertLess(b.position, 10)
        self.h.until(lambda: self.h.system.snapshot.idle)
        self.assertEqual(self.h.system.snapshot.completed_calls, 2)

    def test_cabin_buttons_only_affect_the_selected_car(self):
        self.h.hardware.press_cabin("A", 7)
        self.h.pump()
        self.assertEqual(self.h.system.controllers["A"].snapshot.elevator.target_floor, 7)
        self.assertTrue(self.h.system.controllers["B"].snapshot.idle)
        self.h.hardware.press_cabin("B", 4)
        self.h.pump()
        self.assertEqual(self.h.system.controllers["B"].snapshot.elevator.target_floor, 4)
        self.assertEqual([r.floor for r in self.h.system.controllers["A"].snapshot.cabin_requests], [7])

    def test_duplicate_hall_press_preserves_one_owner_and_original_time(self):
        self.h.hardware.press_hall(3, Direction.UP)
        self.h.pump()
        original = self.h.system.snapshot.calls[0]
        self.h.advance(0.2)
        self.h.hardware.press_hall(3, Direction.UP)
        self.h.pump()
        self.assertEqual(self.h.system.snapshot.calls, (original,))
        self.assertEqual(sum(len(c.snapshot.hall_requests) for c in self.h.system.controllers.values()), 1)

    def test_up_and_down_at_the_same_floor_are_independent(self):
        self.h.hardware.press_hall(5, Direction.UP)
        self.h.hardware.press_hall(5, Direction.DOWN)
        self.h.pump()
        self.assertEqual(len(self.h.system.snapshot.calls), 2)
        self.h.until(lambda: self.h.system.snapshot.idle)
        self.assertEqual({service.direction for service in self.h.system.snapshot.services}, {"up", "down"})
        self.assertEqual(self.h.system.snapshot.completed_calls, 2)

    def test_arrival_does_not_complete_a_call_before_doors_open(self):
        self.h.hardware.press_hall(3, Direction.UP)
        self.h.pump()
        self.h.advance(2)
        self.assertEqual(self.h.system.controllers["A"].snapshot.elevator.doors, DoorState.OPENING)
        self.assertEqual(len(self.h.system.snapshot.calls), 1)
        self.h.advance(0.5)
        self.assertFalse(self.h.system.snapshot.calls)
        self.assertEqual(self.h.system.snapshot.completed_calls, 1)

    def test_call_during_matching_open_doors_can_be_served_immediately(self):
        self.h.hardware.press_hall(3, Direction.UP)
        self.h.pump()
        self.h.until(lambda: self.h.system.snapshot.completed_calls == 1)
        self.h.hardware.press_hall(3, Direction.UP)
        self.h.pump()
        self.assertEqual(self.h.system.snapshot.completed_calls, 2)
        self.assertEqual(self.h.system.snapshot.services[-1].waited_seconds, 0)

    def test_waiting_for_assignment_ack_reserves_work_for_the_next_decision(self):
        h = Harness(initial_floors=(1, 1))
        self.addCleanup(h.system.stop)
        h.dispatcher.handle_event(HallButtonPressed(1, Direction.UP, 0))
        h.dispatcher.handle_event(HallButtonPressed(2, Direction.UP, 0))
        self.assertEqual([call.elevator_id for call in h.system.snapshot.calls], ["A", "B"])
        self.assertEqual([call.state for call in h.system.snapshot.calls], ["assigning", "assigning"])

    def test_direction_and_commitments_can_outweigh_distance(self):
        h = Harness(initial_floors=(5, 3))
        self.addCleanup(h.system.stop)
        h.hardware.press_cabin("A", 1)
        h.hardware.press_cabin("B", 9)
        h.pump()
        h.hardware.press_hall(6, Direction.UP)
        h.pump()
        self.assertEqual(h.system.snapshot.calls[0].elevator_id, "B")

    def test_same_command_ids_in_different_cars_are_scoped_by_car_id(self):
        self.h.hardware.press_cabin("A", 3)
        self.h.hardware.press_cabin("B", 8)
        self.h.pump()
        self.h.until(lambda: self.h.system.snapshot.idle)
        self.assertEqual(self.h.system.controllers["A"].snapshot.elevator.current_floor, 3)
        self.assertEqual(self.h.system.controllers["B"].snapshot.elevator.current_floor, 8)
        self.assertTrue(all(car.controller.fault is None for car in self.h.system.snapshot.cars))

    def test_missing_ack_marks_uncertain_without_assigning_a_second_car(self):
        self.h.dispatcher.handle_event(HallButtonPressed(3, Direction.UP, 0))
        original = self.h.system.snapshot.calls[0]
        self.h.clock.now = 3
        self.h.dispatcher.check_timeouts()
        call = self.h.system.snapshot.calls[0]
        self.assertEqual(call.state, "uncertain")
        self.assertEqual(call.assignment_id, original.assignment_id)
        self.assertTrue(self.h.system.car_queues["B"].empty())
        self.h.pump()
        self.assertEqual(self.h.system.snapshot.calls[0].state, "assigned")

    def test_fault_reassigns_only_after_release_and_keeps_cabin_and_wait_time(self):
        self.h.hardware.press_cabin("A", 7)
        self.h.hardware.press_hall(2, Direction.UP)
        self.h.pump()
        original = self.h.system.snapshot.calls[0]
        self.assertEqual(original.elevator_id, "A")
        self.h.advance(0.2)
        self.h.hardware.fail_car("A")
        self.h.forward_source()
        self.h.drain_dispatcher()
        self.h.drain_cars()
        self.h.drain_dispatcher()
        call = self.h.system.snapshot.calls[0]
        self.assertEqual((call.elevator_id, call.state), ("A", "releasing"))
        self.h.pump()
        call = self.h.system.snapshot.calls[0]
        self.assertEqual(call.elevator_id, "B")
        self.assertNotEqual(call.assignment_id, original.assignment_id)
        self.assertEqual(call.request.created_at, 0)
        self.assertEqual([r.floor for r in self.h.system.controllers["A"].snapshot.cabin_requests], [7])
        self.assertFalse(self.h.system.controllers["A"].snapshot.hall_requests)
        # A late report from the old owner cannot complete the new assignment.
        self.h.dispatcher.handle_event(HallCallServed("A", original.request_id, original.assignment_id, 1))
        self.assertEqual(len(self.h.system.snapshot.calls), 1)
        self.h.until(lambda: not self.h.system.snapshot.calls)
        self.assertEqual(self.h.system.snapshot.services[-1].elevator_id, "B")

    def test_healthy_car_does_not_release_its_assignment(self):
        self.h.hardware.press_hall(3, Direction.UP)
        self.h.pump()
        original = self.h.system.snapshot.calls[0]
        self.h.system.controllers["A"].handle_event(ReleaseHallCalls())
        self.h.pump()
        self.assertEqual(self.h.system.snapshot.calls, (original,))

    def test_missing_release_ack_keeps_ownership_until_late_confirmation(self):
        self.h.hardware.press_hall(3, Direction.UP)
        self.h.pump()
        self.h.hardware.fail_car("A")
        self.h.forward_source()
        self.h.drain_dispatcher()
        self.h.drain_cars()
        self.h.drain_dispatcher()
        self.assertEqual(self.h.system.snapshot.calls[0].state, "releasing")
        self.h.clock.now = 3
        self.h.dispatcher.check_timeouts()
        self.assertEqual(self.h.system.snapshot.calls[0].state, "uncertain")
        self.assertEqual(self.h.system.snapshot.calls[0].elevator_id, "A")
        self.assertTrue(self.h.system.car_queues["B"].empty())
        self.h.pump()
        self.assertEqual(self.h.system.snapshot.calls[0].elevator_id, "B")

    def test_both_faulted_cars_leave_new_calls_waiting(self):
        self.h.hardware.fail_car("A")
        self.h.hardware.fail_car("B")
        self.h.pump()
        self.h.hardware.press_hall(4, Direction.UP)
        self.h.pump()
        self.assertEqual(self.h.system.snapshot.calls[0].state, "waiting")
        self.assertIsNone(self.h.system.snapshot.calls[0].elevator_id)

    def test_old_completion_cannot_clear_a_new_call_at_the_same_floor(self):
        self.h.hardware.press_hall(3, Direction.UP)
        self.h.pump()
        original = self.h.system.snapshot.calls[0]
        self.h.until(lambda: self.h.system.snapshot.idle)
        self.h.hardware.press_hall(3, Direction.DOWN)
        self.h.pump()
        current = self.h.system.snapshot.calls[0]
        self.h.dispatcher.handle_event(HallCallServed("A", original.request_id, original.assignment_id, self.h.clock()))
        self.assertEqual(self.h.system.snapshot.calls, (current,))

    def test_invalid_building_buttons_and_car_ids_are_rejected(self):
        for action in (lambda: self.h.hardware.press_hall(1, Direction.DOWN),
                       lambda: self.h.hardware.press_hall(10, Direction.UP),
                       lambda: self.h.hardware.press_cabin("C", 3),
                       lambda: self.h.hardware.press_cabin("A", 11)):
            with self.assertRaises(ValueError):
                action()

    def test_baseline_runs_both_cars_through_the_same_group_contract(self):
        h = Harness(policy_factory=BaselinePolicy)
        self.addCleanup(h.system.stop)
        h.hardware.press_cabin("A", 7)
        h.hardware.press_cabin("B", 4)
        h.hardware.press_hall(3, Direction.UP)
        h.hardware.press_hall(8, Direction.DOWN)
        h.pump()
        h.until(lambda: h.system.snapshot.idle)
        self.assertEqual(h.system.snapshot.completed_calls, 2)
        self.assertEqual(h.system.controllers["A"].snapshot.elevator.current_floor, 7)
        self.assertEqual(h.system.controllers["B"].snapshot.elevator.current_floor, 4)

    def test_seeded_bursts_finish_without_losing_or_duplicating_hall_calls(self):
        for seed in (11, 22, 33, 44):
            with self.subTest(seed=seed):
                h = Harness()
                self.addCleanup(h.system.stop)
                random = Random(seed)
                for _ in range(25):
                    floor = random.randint(1, 10)
                    direction = Direction.UP if floor == 1 else Direction.DOWN if floor == 10 else random.choice(tuple(Direction))
                    h.hardware.press_hall(floor, direction)
                    h.hardware.press_cabin(random.choice(("A", "B")), random.randint(1, 10))
                h.pump()
                expected = h.system.snapshot.completed_calls + len(h.system.snapshot.calls)
                h.until(lambda: h.system.snapshot.idle, seconds=180)
                self.assertEqual(h.system.snapshot.completed_calls, expected)
                self.assertEqual(len({record.request_id for record in h.system.snapshot.services}), expected)
                self.assertTrue(all(not car.controller.fault for car in h.system.snapshot.cars))


class GroupRuntimeTests(unittest.TestCase):
    def test_four_real_workers_serve_both_cars_and_stop_cleanly(self):
        system = ElevatorGroupSystem(seconds_per_floor=0.06, door_seconds=0.02, dwell_seconds=0.04)
        self.addCleanup(system.stop)
        system.start()
        self.assertEqual(len(system.workers), 4)
        self.assertTrue(all(worker.is_alive() for worker in system.workers))
        system.hardware.press_hall(3, Direction.UP)
        system.hardware.press_hall(9, Direction.DOWN)
        deadline = time.monotonic() + 5
        while system.snapshot.completed_calls != 2 or not system.snapshot.idle:
            system.hardware.tick()
            self.assertIsNone(system.snapshot.error)
            self.assertTrue(all(car.controller.fault is None for car in system.snapshot.cars))
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        self.assertEqual({record.elevator_id for record in system.snapshot.services}, {"A", "B"})
        system.stop()
        self.assertTrue(all(not worker.is_alive() for worker in system.workers))
        with self.assertRaises(RuntimeError):
            system.start()

    def test_stop_before_start_and_empty_queue_shutdown(self):
        system = ElevatorGroupSystem()
        system.stop()
        with self.assertRaises(RuntimeError):
            system.start()
        system = ElevatorGroupSystem()
        system.start()
        system.stop()
        self.assertTrue(all(not worker.is_alive() for worker in system.workers))

    def test_listener_failure_is_visible_and_can_be_shut_down(self):
        system = ElevatorGroupSystem()
        self.addCleanup(system.stop)

        def fail():
            raise RuntimeError("Disconnected test source")

        system.hardware.wait_for_event = fail
        system.start()
        state = system.dispatcher.wait_for(lambda snapshot: snapshot.error is not None)
        self.assertIn("Disconnected test source", state.error)
        system.stop()
        self.assertTrue(all(not worker.is_alive() for worker in system.workers))


if __name__ == "__main__":
    unittest.main()
