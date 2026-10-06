"""Simulation physics and controller integration, without a display or sleeps."""

import unittest

from elevator_controller import Direction, DoorState, ElevatorController, MotionState
from elevator_controller.events import ArrivedAtFloor, DoorsClosed, DoorsOpened, FloorPassed, HallButtonPressed
from elevator_simulator.hardware import SimulatorHardware


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def collect_events(hardware):
    # A wake-up marker lets tests drain the public blocking API deterministically.
    hardware.cancel_wait()
    result = []
    while True:
        event = hardware.wait_for_event()
        if event is None:
            return result
        result.append(event)


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hardware = SimulatorHardware(seconds_per_floor=1, door_seconds=1, clock=self.clock)

    def advance(self, seconds):
        self.clock.advance(seconds)
        self.hardware.tick()

    def test_progress_is_continuous_and_not_teleported_to_target(self):
        self.assertTrue(self.hardware.move_to_floor(4, 1))
        self.advance(0.5)
        self.assertAlmostEqual(self.hardware.snapshot.position, 1.5)
        self.assertEqual(self.hardware.snapshot.current_floor, 1)
        self.assertEqual(self.hardware.snapshot.motion, MotionState.MOVING)
        self.assertFalse(collect_events(self.hardware))

    def test_long_frame_reports_all_crossings_before_one_arrival(self):
        self.hardware.move_to_floor(5, 10)
        self.advance(10)
        self.assertEqual(
            collect_events(self.hardware),
            [FloorPassed(2, 10), FloorPassed(3, 10), FloorPassed(4, 10), ArrivedAtFloor(5, 10)],
        )
        self.assertEqual(self.hardware.snapshot.position, 5)
        self.advance(1)
        self.assertFalse(collect_events(self.hardware))

    def test_downward_crossings_are_ordered_and_do_not_duplicate_start_floor(self):
        self.hardware.move_to_floor(5, 1)
        self.advance(4)
        collect_events(self.hardware)
        self.assertTrue(self.hardware.move_to_floor(2, 2))
        self.advance(1)
        self.assertEqual(collect_events(self.hardware), [FloorPassed(4, 2)])
        self.advance(2)
        self.assertEqual(collect_events(self.hardware), [FloorPassed(3, 2), ArrivedAtFloor(2, 2)])

    def test_accepted_retarget_stops_at_intermediate_floor(self):
        self.hardware.move_to_floor(8, 1)
        self.advance(0.5)
        self.assertTrue(self.hardware.try_update_target(3, 1))
        self.advance(10)
        self.assertEqual(collect_events(self.hardware), [FloorPassed(2, 1), ArrivedAtFloor(3, 1)])
        self.assertEqual(self.hardware.snapshot.position, 3)

    def test_late_retarget_uses_physical_position_and_keeps_old_target(self):
        self.hardware.move_to_floor(8, 1)
        self.advance(1.9)  # position 2.9: less than a quarter-floor to stop at 3
        self.assertFalse(self.hardware.try_update_target(3, 1))
        self.assertEqual(self.hardware.snapshot.target_floor, 8)
        self.assertEqual(self.hardware.snapshot.motion, MotionState.MOVING)

    def test_command_advances_time_even_if_gui_has_not_refreshed(self):
        self.hardware.move_to_floor(8, 1)
        self.clock.advance(2.5)  # No tick yet: physical position must still be 3.5.
        self.assertFalse(self.hardware.try_update_target(3, 1))
        self.assertAlmostEqual(self.hardware.snapshot.position, 3.5)
        self.assertEqual(self.hardware.snapshot.target_floor, 8)

    def test_completed_arrival_cannot_be_retargeted_before_controller_reads_it(self):
        self.hardware.move_to_floor(3, 1)
        self.clock.advance(3)
        self.assertFalse(self.hardware.try_update_target(2, 1))
        self.assertEqual(collect_events(self.hardware), [FloorPassed(2, 1), ArrivedAtFloor(3, 1)])

    def test_retarget_cannot_reverse_or_use_a_stale_command(self):
        self.hardware.move_to_floor(7, 1)
        self.advance(2.5)
        self.assertFalse(self.hardware.try_update_target(2, 1))
        self.assertFalse(self.hardware.try_update_target(5, 99))
        self.assertEqual(self.hardware.snapshot.target_floor, 7)

    def test_pickup_approach_can_extend_without_reversing(self):
        self.hardware.move_to_floor(3, 1)
        self.advance(0.5)
        self.assertTrue(self.hardware.try_update_target(5, 1))
        self.advance(10)
        self.assertEqual(
            collect_events(self.hardware),
            [FloorPassed(2, 1), FloorPassed(3, 1), FloorPassed(4, 1), ArrivedAtFloor(5, 1)],
        )

    def test_extension_is_rejected_when_too_close_to_the_original_stop(self):
        self.hardware.move_to_floor(3, 1)
        self.advance(1.9)
        self.assertFalse(self.hardware.try_update_target(5, 1))
        self.assertEqual(self.hardware.snapshot.target_floor, 3)

    def test_door_animation_reports_completion_once_and_blocks_movement(self):
        self.assertTrue(self.hardware.open_doors(1))
        self.assertFalse(self.hardware.move_to_floor(7, 2))
        self.advance(0.5)
        self.assertAlmostEqual(self.hardware.snapshot.door_open_fraction, 0.5)
        self.assertEqual(self.hardware.snapshot.doors, DoorState.OPENING)
        self.assertFalse(collect_events(self.hardware))
        self.advance(0.5)
        self.assertEqual(collect_events(self.hardware), [DoorsOpened(1)])
        self.assertFalse(self.hardware.move_to_floor(7, 2))
        self.assertTrue(self.hardware.close_doors(3))
        self.advance(0.5)
        self.assertAlmostEqual(self.hardware.snapshot.door_open_fraction, 0.5)
        self.assertFalse(self.hardware.move_to_floor(7, 4))
        self.advance(0.5)
        self.assertEqual(collect_events(self.hardware), [DoorsClosed(3)])
        self.assertTrue(self.hardware.move_to_floor(7, 4))
        self.assertFalse(self.hardware.open_doors(5))

    def test_floor_buttons_respect_building_boundaries_and_capture_time(self):
        self.clock.advance(4)
        self.hardware.press_hall(3, Direction.UP)
        self.assertEqual(collect_events(self.hardware), [HallButtonPressed(3, Direction.UP, 4)])
        for floor_number, direction in ((1, Direction.DOWN), (10, Direction.UP), (11, Direction.DOWN)):
            with self.subTest(floor=floor_number), self.assertRaises(ValueError):
                self.hardware.press_hall(floor_number, direction)
        with self.assertRaises(ValueError):
            self.hardware.press_cabin(11)

    def test_shutdown_freezes_simulation_and_rejects_new_commands(self):
        self.hardware.move_to_floor(8, 1)
        self.advance(1)
        before = self.hardware.snapshot.position
        collect_events(self.hardware)
        self.hardware.shutdown()
        self.advance(100)
        self.hardware.press_cabin(3)
        self.assertEqual(self.hardware.snapshot.position, before)
        self.assertFalse(self.hardware.try_update_target(3, 1))
        self.assertFalse(self.hardware.open_doors(2))
        self.assertFalse(collect_events(self.hardware))

    def test_invalid_simulation_durations_are_rejected(self):
        for value in (0, -1, float("inf"), float("nan"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SimulatorHardware(seconds_per_floor=value)


class ControllerSimulationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hardware = SimulatorHardware(seconds_per_floor=1, door_seconds=1, clock=self.clock)
        self.controller = ElevatorController(self.hardware, 10, clock=self.clock, door_dwell_seconds=2)

    def pump(self, seconds=0):
        self.clock.advance(seconds)
        self.hardware.tick()
        for event in collect_events(self.hardware):
            self.controller.handle_event(event)
        self.controller.check_timeouts()
        self.assertIsNone(self.controller.snapshot.fault)

    def complete_service(self):
        self.pump(1)  # doors opened
        self.pump(2)  # dwell expired, close requested
        self.pump(1)  # doors closed, next movement allowed

    def test_hall_pickup_then_cabin_destination_returns_to_idle(self):
        self.hardware.press_hall(3, Direction.UP)
        self.pump()
        self.pump(2)
        self.assertEqual(self.controller.snapshot.elevator.current_floor, 3)
        self.pump(1)
        self.hardware.press_cabin(7)
        self.pump()
        self.assertEqual(self.hardware.snapshot.doors, DoorState.OPEN)
        self.pump(2)
        self.pump(1)
        self.assertEqual(self.hardware.snapshot.target_floor, 7)
        self.pump(4)
        self.complete_service()
        self.assertTrue(self.controller.snapshot.idle)
        self.assertEqual(self.hardware.snapshot.position, 7)

    def test_committed_pickup_survives_intermediate_stop_and_new_reverse_request(self):
        self.hardware.press_hall(8, Direction.UP)
        self.pump()
        self.hardware.press_cabin(7)
        self.pump()
        self.assertEqual(self.hardware.snapshot.target_floor, 7)
        self.pump(6)
        self.pump(1)
        self.hardware.press_cabin(1)
        self.pump()
        self.pump(2)
        self.pump(1)
        self.assertEqual(self.hardware.snapshot.target_floor, 8)
        self.pump(1)
        self.complete_service()
        self.assertEqual(self.hardware.snapshot.target_floor, 1)
        self.pump(7)
        self.complete_service()
        self.assertTrue(self.controller.snapshot.idle)

    def test_late_pickup_remains_pending_and_is_served_after_original_destination(self):
        self.hardware.press_cabin(7)
        self.pump()
        self.pump(1.9)
        self.hardware.press_hall(3, Direction.UP)
        self.pump()
        self.assertEqual(self.hardware.snapshot.target_floor, 7)
        self.assertEqual(len(self.controller.snapshot.hall_requests), 1)
        self.pump(4.2)
        self.complete_service()
        self.assertEqual(self.hardware.snapshot.target_floor, 3)
        self.pump(4)
        self.complete_service()
        self.assertTrue(self.controller.snapshot.idle)

    def test_empty_approach_from_seven_picks_up_one_before_three_then_serves_six(self):
        # Move to 7 first using the same public controls as the GUI.
        self.hardware.press_cabin(7)
        self.pump()
        self.pump(6)
        self.complete_service()
        self.assertTrue(self.controller.snapshot.idle)

        self.hardware.press_hall(3, Direction.UP)
        self.pump()
        self.assertEqual(self.hardware.snapshot.target_floor, 3)
        self.pump(0.5)
        self.hardware.press_hall(1, Direction.UP)
        self.pump()
        self.assertEqual(self.hardware.snapshot.target_floor, 1)
        self.pump(5.5)
        self.assertEqual(self.hardware.snapshot.position, 1)
        self.complete_service()
        self.assertEqual(self.hardware.snapshot.target_floor, 3)
        self.pump(2)
        self.pump(1)
        self.hardware.press_cabin(6)
        self.pump()
        self.pump(2)
        self.pump(1)
        self.assertEqual(self.hardware.snapshot.target_floor, 6)
        self.pump(3)
        self.complete_service()
        self.assertTrue(self.controller.snapshot.idle)
        arrivals = [record.text for record in self.hardware.snapshot.activity if record.text.startswith("Arrived")]
        self.assertEqual(arrivals, [
            "Arrived at floor 7.", "Arrived at floor 1.",
            "Arrived at floor 3.", "Arrived at floor 6.",
        ])


if __name__ == "__main__":
    unittest.main()
