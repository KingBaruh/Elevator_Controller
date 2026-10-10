"""Behavioral tests with manual time and explicit hardware completions."""

import unittest

from elevator_controller import Direction, DoorState, Elevator, ElevatorController, MotionState
from elevator_controller.events import (
    ArrivedAtFloor, CabinButtonPressed, DoorsClosed, DoorsOpened,
    FloorPassed, HallButtonPressed, HardwareFault,
)
from elevator_controller.fake_hardware import FakeHardware


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.hardware = FakeHardware()
        self.controller = ElevatorController(
            self.hardware, 10, clock=self.clock,
            door_dwell_seconds=3, operation_timeout=30,
        )

    @property
    def state(self):
        return self.controller.snapshot

    def send(self, event):
        self.hardware.emit(event)
        self.controller.handle_event(self.hardware.wait_for_event())

    def commands(self, name):
        return [c for c in self.hardware.commands if c.name == name]

    def last(self, name):
        return self.commands(name)[-1]

    def arrive(self, floor):
        self.send(ArrivedAtFloor(floor, self.last("move_to_floor").arguments[1]))

    def open(self):
        self.send(DoorsOpened(self.last("open_doors").arguments[0]))

    def finish_dwell(self):
        self.clock.advance(3)
        self.controller.check_timeouts()

    def close(self):
        self.send(DoorsClosed(self.last("close_doors").arguments[0]))

    def test_full_trip_waits_for_each_completion_and_becomes_idle(self):
        self.send(HallButtonPressed(3, Direction.UP))
        self.assertEqual(self.last("move_to_floor").arguments[0], 3)
        self.assertEqual(self.state.elevator.current_floor, 1)
        self.assertEqual(self.state.elevator.motion, MotionState.MOVING)
        self.assertEqual(len(self.state.hall_requests), 1)
        self.assertFalse(self.commands("open_doors"))

        self.arrive(3)
        self.assertEqual(self.state.elevator.doors, DoorState.OPENING)
        self.assertEqual(len(self.state.hall_requests), 1)
        self.open()
        self.assertFalse(self.state.hall_requests)
        self.assertFalse(self.state.idle)  # Door service is still active.
        self.clock.advance(2)
        self.controller.check_timeouts()
        self.assertFalse(self.commands("close_doors"))
        self.clock.advance(1)
        self.controller.check_timeouts()
        self.assertEqual(self.state.elevator.doors, DoorState.CLOSING)
        self.close()
        self.assertTrue(self.state.idle)
        self.assertEqual(self.last("update_displays").arguments, (3, None))

    def test_internal_request_while_doors_open_waits_for_closed_confirmation(self):
        self.send(HallButtonPressed(3, Direction.UP))
        self.arrive(3)
        self.open()
        self.send(CabinButtonPressed(7))
        self.assertEqual(len(self.commands("move_to_floor")), 1)
        self.finish_dwell()
        self.assertEqual(len(self.commands("move_to_floor")), 1)
        self.close()
        self.assertEqual(self.last("move_to_floor").arguments[0], 7)

    def test_committed_eighth_floor_is_served_before_new_first_floor_request(self):
        self.send(HallButtonPressed(8, Direction.UP))
        self.send(CabinButtonPressed(7))
        self.assertEqual(self.last("try_update_target").arguments[0], 7)
        self.arrive(7)
        self.open()
        self.send(CabinButtonPressed(1))
        self.finish_dwell()
        self.close()
        self.assertEqual(self.last("move_to_floor").arguments[0], 8)
        self.arrive(8)
        self.open()
        self.finish_dwell()
        self.close()
        self.assertEqual(self.last("move_to_floor").arguments[0], 1)
        self.assertIsNone(self.state.fault)

    def start_at_seven(self):
        self.hardware = FakeHardware(Elevator(7))
        self.controller = ElevatorController(
            self.hardware, 10, clock=self.clock, door_dwell_seconds=3,
        )

    def test_cabin_seven_then_hall_eight_is_served_before_reverse_cabin_five(self):
        for hall_direction in (Direction.UP, Direction.DOWN):
            with self.subTest(direction=hall_direction):
                self.setUp()
                self.send(CabinButtonPressed(7))
                self.send(HallButtonPressed(8, hall_direction))
                self.arrive(7)
                self.open()
                self.send(CabinButtonPressed(5))
                self.finish_dwell()
                self.close()
                self.assertEqual(self.state.elevator.target_floor, 8)
                self.arrive(8)
                self.open()
                self.assertFalse(self.state.hall_requests)
                self.finish_dwell()
                self.close()
                self.assertEqual(self.state.elevator.target_floor, 5)
                self.arrive(5)
                self.open()
                self.finish_dwell()
                self.close()
                self.assertTrue(self.state.idle)

    def test_request_while_doors_open_continues_sweep_to_eight_then_ten_before_five(self):
        self.send(CabinButtonPressed(7))
        self.arrive(7)
        self.open()
        self.assertIsNone(self.state.route)
        self.send(CabinButtonPressed(5))
        self.send(HallButtonPressed(8, Direction.UP))
        self.finish_dwell()
        self.close()
        self.assertEqual(self.state.elevator.target_floor, 8)
        self.arrive(8)
        self.open()
        self.send(CabinButtonPressed(10))
        self.finish_dwell()
        self.close()
        self.assertEqual(self.state.elevator.target_floor, 10)
        self.arrive(10)
        self.open()
        self.finish_dwell()
        self.close()
        self.assertEqual(self.state.elevator.target_floor, 5)

    def test_opposite_pickup_remembers_service_direction_not_arrival_direction(self):
        self.send(HallButtonPressed(6, Direction.DOWN))
        self.send(HallButtonPressed(7, Direction.UP))
        self.arrive(6)
        self.open()
        self.assertEqual(self.state.elevator.last_direction, Direction.DOWN)
        self.send(CabinButtonPressed(1))
        self.finish_dwell()
        self.close()
        self.assertEqual(self.state.elevator.target_floor, 1)

    def test_idle_clears_sweep_direction_before_a_new_cabin_request(self):
        self.send(CabinButtonPressed(7))
        self.arrive(7)
        self.open()
        self.finish_dwell()
        self.close()
        self.assertTrue(self.state.idle)
        self.assertIsNone(self.state.elevator.last_direction)
        self.send(CabinButtonPressed(5))
        self.assertEqual(self.state.elevator.target_floor, 5)

    def test_seven_to_one_then_three_then_six_pickup_sequence(self):
        self.start_at_seven()
        self.send(HallButtonPressed(3, Direction.UP))
        self.assertEqual(self.state.elevator.target_floor, 3)
        self.send(HallButtonPressed(1, Direction.UP))
        self.assertTrue(self.last("try_update_target").accepted)
        self.assertEqual(self.state.elevator.target_floor, 1)
        self.assertEqual({r.floor for r in self.state.hall_requests}, {1, 3})

        self.arrive(1)
        self.open()
        self.assertEqual([r.floor for r in self.state.hall_requests], [3])
        self.finish_dwell()
        self.close()
        self.assertEqual(self.state.elevator.target_floor, 3)
        self.arrive(3)
        self.open()
        self.send(CabinButtonPressed(6))
        self.finish_dwell()
        self.close()
        self.assertEqual(self.state.elevator.target_floor, 6)
        self.arrive(6)
        self.open()
        self.finish_dwell()
        self.close()
        self.assertTrue(self.state.idle)

    def test_hardware_rejected_extension_keeps_original_pickup_and_both_requests(self):
        self.start_at_seven()
        self.send(HallButtonPressed(3, Direction.UP))
        original = self.state.route
        self.hardware.accept_retargets = False
        self.send(HallButtonPressed(1, Direction.UP))
        self.assertFalse(self.last("try_update_target").accepted)
        self.assertEqual(self.state.route, original)
        self.assertEqual(self.state.elevator.target_floor, 3)
        self.assertEqual({r.floor for r in self.state.hall_requests}, {1, 3})
        self.assertIsNone(self.state.fault)

    def test_arrival_queued_before_extension_keeps_original_stop(self):
        self.start_at_seven()
        self.send(HallButtonPressed(3, Direction.UP))
        movement_id = self.last("move_to_floor").arguments[1]
        self.hardware.emit(ArrivedAtFloor(3, movement_id))
        self.controller.handle_event(HallButtonPressed(1, Direction.UP))
        self.assertFalse(self.last("try_update_target").accepted)
        self.controller.handle_event(self.hardware.wait_for_event())
        self.assertEqual(self.state.elevator.current_floor, 3)
        self.assertEqual(self.state.elevator.doors, DoorState.OPENING)
        self.assertIsNone(self.state.fault)

    def test_after_arrival_internal_destination_keeps_priority_over_new_lower_call(self):
        self.start_at_seven()
        self.send(HallButtonPressed(3, Direction.UP))
        self.arrive(3)
        self.send(HallButtonPressed(1, Direction.UP))
        self.open()
        self.send(CabinButtonPressed(6))
        self.finish_dwell()
        self.close()
        self.assertFalse(self.commands("try_update_target"))
        self.assertEqual(self.state.elevator.target_floor, 6)
        self.assertEqual([r.floor for r in self.state.hall_requests], [1])

    def test_accepted_intermediate_stop_keeps_original_destination(self):
        self.send(CabinButtonPressed(7))
        self.send(HallButtonPressed(3, Direction.UP))
        self.assertEqual(self.state.elevator.target_floor, 3)
        self.assertEqual([s.floor for s in self.state.route.stops], [3, 7])
        self.arrive(3)
        self.open()
        self.finish_dwell()
        self.close()
        self.assertEqual(self.last("move_to_floor").arguments[0], 7)

    def test_rejected_retarget_preserves_route_and_later_serves_waiting_call(self):
        self.hardware.accept_retargets = False
        self.send(CabinButtonPressed(7))
        self.send(HallButtonPressed(3, Direction.UP))
        self.assertFalse(self.last("try_update_target").accepted)
        self.assertEqual(self.state.elevator.target_floor, 7)
        self.assertEqual([s.floor for s in self.state.route.stops], [7])
        self.assertEqual(len(self.state.hall_requests), 1)
        self.arrive(7)
        self.open()
        self.finish_dwell()
        self.close()
        self.assertEqual(self.last("move_to_floor").arguments[0], 3)

    def test_physical_arrival_queued_before_retarget_is_not_overwritten(self):
        self.send(CabinButtonPressed(7))
        movement_id = self.last("move_to_floor").arguments[1]
        # Arrival has occurred physically, but the controller has not read it.
        self.hardware.emit(ArrivedAtFloor(7, movement_id))
        self.controller.handle_event(HallButtonPressed(3, Direction.UP))
        self.assertFalse(self.last("try_update_target").accepted)
        self.assertEqual(self.state.elevator.target_floor, 7)
        self.controller.handle_event(self.hardware.wait_for_event())
        self.assertEqual(self.state.elevator.doors, DoorState.OPENING)
        self.assertIsNone(self.state.fault)

    def test_opposite_hall_direction_remains_pending_after_doors_open(self):
        self.send(HallButtonPressed(3, Direction.UP))
        self.send(HallButtonPressed(3, Direction.DOWN))
        self.arrive(3)
        self.open()
        self.assertEqual([(r.floor, r.direction) for r in self.state.hall_requests], [(3, Direction.DOWN)])

    def test_terminal_pickup_displays_service_direction_not_arrival_direction(self):
        self.send(HallButtonPressed(8, Direction.DOWN))
        self.assertEqual(self.last("update_displays").arguments, (1, Direction.UP))
        self.arrive(8)
        self.assertEqual(self.last("update_displays").arguments, (8, Direction.DOWN))

    def test_call_on_current_floor_opens_doors_without_movement(self):
        self.send(HallButtonPressed(1, Direction.UP))
        self.assertFalse(self.commands("move_to_floor"))
        self.assertEqual(len(self.commands("open_doors")), 1)
        self.open()
        self.assertFalse(self.state.hall_requests)

    def test_same_floor_buttons_during_open_doors_are_served_without_extending_dwell(self):
        self.send(HallButtonPressed(3, Direction.UP))
        self.arrive(3)
        self.open()
        self.clock.advance(2)
        self.send(HallButtonPressed(3, Direction.UP))
        self.send(CabinButtonPressed(3))
        self.assertFalse(self.state.hall_requests)
        self.assertFalse(self.state.cabin_requests)
        self.clock.advance(1)
        self.controller.check_timeouts()
        self.assertEqual(self.state.elevator.doors, DoorState.CLOSING)
        self.assertEqual(len(self.commands("open_doors")), 1)

    def test_same_floor_request_during_closing_is_served_after_closed_confirmation(self):
        self.send(HallButtonPressed(3, Direction.UP))
        self.arrive(3)
        self.open()
        self.finish_dwell()
        self.send(HallButtonPressed(3, Direction.UP))
        self.assertEqual(len(self.commands("open_doors")), 1)
        self.close()
        self.assertEqual(len(self.commands("open_doors")), 2)
        self.assertEqual(len(self.commands("move_to_floor")), 1)

    def test_duplicate_press_does_not_send_more_commands_or_reset_age(self):
        self.send(HallButtonPressed(7, Direction.UP))
        commands_before = self.hardware.commands
        self.clock.advance(2)
        self.send(HallButtonPressed(7, Direction.UP))
        self.assertEqual(self.hardware.commands, commands_before)
        self.assertEqual(self.state.hall_requests[0].created_at, 0)

    def test_floor_pass_updates_display_but_does_not_complete_requests(self):
        self.send(CabinButtonPressed(7))
        command_id = self.last("move_to_floor").arguments[1]
        self.send(FloorPassed(3, command_id))
        self.assertEqual(self.state.elevator.current_floor, 3)
        self.assertEqual(self.last("update_displays").arguments, (3, Direction.UP))
        self.assertEqual(len(self.state.cabin_requests), 1)
        self.assertFalse(self.commands("open_doors"))
        self.send(HallButtonPressed(3, Direction.UP))
        self.assertFalse(self.commands("try_update_target"))

    def test_old_completions_do_not_affect_new_movement(self):
        self.send(CabinButtonPressed(3))
        old_motion_id = self.last("move_to_floor").arguments[1]
        self.arrive(3)
        old_open_id = self.last("open_doors").arguments[0]
        self.open()
        self.send(CabinButtonPressed(7))
        self.finish_dwell()
        self.close()
        before = self.state
        self.send(ArrivedAtFloor(3, old_motion_id))
        self.send(FloorPassed(2, old_motion_id))
        self.send(DoorsOpened(old_open_id))
        self.assertEqual(self.state, before)

    def test_duplicate_open_confirmation_does_not_reset_dwell(self):
        self.send(CabinButtonPressed(3))
        self.arrive(3)
        opened = DoorsOpened(self.last("open_doors").arguments[0])
        self.send(opened)
        self.clock.advance(2)
        self.send(opened)
        self.clock.advance(1)
        self.controller.check_timeouts()
        self.assertEqual(self.state.elevator.doors, DoorState.CLOSING)

    def test_invalid_passenger_input_is_rejected_without_faulting(self):
        for event in (
            CabinButtonPressed(11), CabinButtonPressed(0),
            HallButtonPressed(1, Direction.DOWN),
            HallButtonPressed(10, Direction.UP),
            HallButtonPressed(3, Direction.UP, created_at=100),
        ):
            self.send(event)
            self.assertIsNotNone(self.state.rejected_input)
            self.assertIsNone(self.state.fault)
        self.assertTrue(self.state.idle)
        self.send(CabinButtonPressed(7))
        self.assertEqual(self.state.elevator.target_floor, 7)

    def test_missing_arrival_latches_fault_without_losing_request(self):
        self.send(CabinButtonPressed(7))
        self.clock.advance(30)
        self.controller.check_timeouts()
        self.assertIn("timed out", self.state.fault)
        self.assertTrue(self.state.elevator.faulted)
        self.assertEqual(self.state.elevator.motion, MotionState.MOVING)
        self.assertEqual(len(self.state.cabin_requests), 1)
        self.assertFalse(self.commands("open_doors"))

    def test_missing_open_confirmation_keeps_request_pending(self):
        self.send(CabinButtonPressed(3))
        self.arrive(3)
        self.clock.advance(30)
        self.controller.check_timeouts()
        self.assertTrue(self.state.elevator.faulted)
        self.assertEqual(len(self.state.cabin_requests), 1)

    def test_missing_closed_confirmation_never_starts_next_trip(self):
        self.send(CabinButtonPressed(3))
        self.arrive(3)
        self.open()
        self.send(CabinButtonPressed(7))
        self.finish_dwell()
        self.clock.advance(30)
        self.controller.check_timeouts()
        self.assertTrue(self.state.elevator.faulted)
        self.assertEqual(len(self.commands("move_to_floor")), 1)

    def test_fault_suppresses_commands_and_retains_new_requests(self):
        self.send(CabinButtonPressed(7))
        self.send(HardwareFault("Drive failure"))
        before = self.hardware.commands
        self.send(CabinButtonPressed(8))
        self.clock.advance(100)
        self.controller.check_timeouts()
        self.assertEqual(self.hardware.commands, before)
        self.assertEqual({r.floor for r in self.state.cabin_requests}, {7, 8})

    def test_rejected_move_is_visible_fault_and_does_not_claim_motion(self):
        self.hardware.accept_moves = False
        self.send(CabinButtonPressed(7))
        self.assertIn("rejected", self.state.fault)
        self.assertEqual(self.state.elevator.motion, MotionState.STOPPED)
        self.assertEqual(len(self.state.cabin_requests), 1)

    def test_rejected_door_open_does_not_complete_request(self):
        self.hardware.accept_doors = False
        self.send(CabinButtonPressed(3))
        self.arrive(3)
        self.assertIn("door-open", self.state.fault)
        self.assertEqual(len(self.state.cabin_requests), 1)

    def test_wrong_arrival_for_current_command_faults_without_opening_doors(self):
        self.send(CabinButtonPressed(7))
        self.arrive(6)
        self.assertIn("Arrival", self.state.fault)
        self.assertFalse(self.commands("open_doors"))

    def test_hardware_exception_is_reported_without_losing_request(self):
        def disconnected(*_args):
            raise OSError("Connection lost")
        self.hardware.move_to_floor = disconnected
        self.send(CabinButtonPressed(7))
        self.assertIn("Connection lost", self.state.fault)
        self.assertEqual(len(self.state.cabin_requests), 1)

    def test_aging_takes_effect_after_current_route_and_door_service_finish(self):
        # Use a longer operation timeout so this trip can take over 60 seconds.
        hardware = FakeHardware()
        controller = ElevatorController(hardware, 10, clock=self.clock, operation_timeout=120)
        controller.handle_event(CabinButtonPressed(7))
        controller.handle_event(HallButtonPressed(2, Direction.DOWN))
        self.clock.advance(61)
        movement = next(c for c in hardware.commands if c.name == "move_to_floor")
        hardware.emit(ArrivedAtFloor(7, movement.arguments[1]))
        controller.handle_event(hardware.wait_for_event())
        opened = next(c for c in hardware.commands if c.name == "open_doors")
        hardware.emit(DoorsOpened(opened.arguments[0]))
        controller.handle_event(hardware.wait_for_event())
        controller.handle_event(CabinButtonPressed(9))
        self.clock.advance(3)
        controller.check_timeouts()
        closed = next(c for c in hardware.commands if c.name == "close_doors")
        hardware.emit(DoorsClosed(closed.arguments[0]))
        controller.handle_event(hardware.wait_for_event())
        self.assertEqual(controller.snapshot.elevator.target_floor, 2)

    def test_startup_requires_known_stationary_closed_state(self):
        for initial in (
            Elevator(1, doors=DoorState.OPEN),
            Elevator(1, motion=MotionState.MOVING),
            Elevator(1, faulted=True),
            Elevator(11),
        ):
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                ElevatorController(FakeHardware(initial), 10)


if __name__ == "__main__":
    unittest.main()
