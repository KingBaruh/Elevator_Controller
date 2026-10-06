"""Integration tests use two real workers and bounded condition waits, no sleeps."""

import unittest
from queue import Queue
from threading import Event

from elevator_controller import Direction, DoorState, ElevatorSystem
from elevator_controller.events import ArrivedAtFloor, CabinButtonPressed, DoorsClosed, DoorsOpened, HallButtonPressed
from elevator_controller.fake_hardware import FakeHardware
from elevator_controller.listener import HardwareListener


class RuntimeTests(unittest.TestCase):
    def start_system(self, **options):
        hardware = FakeHardware()
        system = ElevatorSystem(hardware, 10, **options)
        system.start()
        self.addCleanup(system.stop)
        return hardware, system

    def test_real_workers_complete_trip_with_timer_and_exit_cleanly(self):
        hardware, system = self.start_system(door_dwell_seconds=0.02)
        self.assertEqual(len(system.workers), 2)
        self.assertTrue(all(worker.is_alive() for worker in system.workers))
        hardware.emit(HallButtonPressed(3, Direction.UP))
        movement = hardware.wait_for_command("move_to_floor")
        hardware.emit(ArrivedAtFloor(3, movement.arguments[1]))
        opening = hardware.wait_for_command("open_doors")
        hardware.emit(DoorsOpened(opening.arguments[0]))
        closing = hardware.wait_for_command("close_doors")
        hardware.emit(DoorsClosed(closing.arguments[0]))
        final = system.controller.wait_for(lambda s: s.idle and s.elevator.current_floor == 3)
        self.assertIsNone(final.fault)
        system.stop()
        self.assertTrue(all(not worker.is_alive() for worker in system.workers))

    def test_buttons_are_processed_while_hardware_movement_is_outstanding(self):
        hardware, system = self.start_system()
        hardware.emit(CabinButtonPressed(7))
        hardware.wait_for_command("move_to_floor")
        hardware.emit(HallButtonPressed(3, Direction.UP))
        retarget = hardware.wait_for_command("try_update_target")
        self.assertTrue(retarget.accepted)
        state = system.controller.wait_for(lambda s: s.elevator.target_floor == 3)
        self.assertEqual([stop.floor for stop in state.route.stops], [3, 7])

    def test_real_workers_queue_burst_deduplicates_without_losing_other_inputs(self):
        hardware, system = self.start_system()
        hardware.emit(CabinButtonPressed(7))
        hardware.wait_for_command("move_to_floor")
        for _ in range(20):
            hardware.emit(HallButtonPressed(8, Direction.DOWN))
        hardware.emit(CabinButtonPressed(9))
        state = system.controller.wait_for(lambda s: any(r.floor == 9 for r in s.cabin_requests))
        self.assertEqual(len(state.hall_requests), 1)
        self.assertEqual({r.floor for r in state.cabin_requests}, {7, 9})
        self.assertIsNone(state.fault)

    def test_hardware_timeout_runs_without_new_events(self):
        hardware, system = self.start_system(operation_timeout=0.02)
        hardware.emit(CabinButtonPressed(7))
        state = system.controller.wait_for(lambda s: s.elevator.faulted)
        self.assertIn("timed out", state.fault)

    def test_shutdown_wakes_both_workers_when_their_queues_are_empty(self):
        _, system = self.start_system()
        system.stop()
        self.assertTrue(all(not worker.is_alive() for worker in system.workers))
        with self.assertRaises(RuntimeError):
            system.start()

    def test_shutdown_during_dwell_does_not_wait_for_dwell_to_expire(self):
        hardware, system = self.start_system(door_dwell_seconds=100)
        hardware.emit(HallButtonPressed(1, Direction.UP))
        opening = hardware.wait_for_command("open_doors")
        hardware.emit(DoorsOpened(opening.arguments[0]))
        system.controller.wait_for(lambda s: s.elevator.doors is DoorState.OPEN)
        system.stop()
        self.assertTrue(all(not worker.is_alive() for worker in system.workers))
        self.assertFalse(any(c.name == "close_doors" for c in hardware.commands))

    def test_listener_failure_is_reported_to_controller(self):
        hardware = FakeHardware()
        def failed_reader():
            raise OSError("Disconnected input bus")
        hardware.wait_for_event = failed_reader
        with ElevatorSystem(hardware, 10) as system:
            state = system.controller.wait_for(lambda s: s.elevator.faulted)
            self.assertIn("Disconnected input bus", state.fault)

    def test_listener_stamps_request_at_reception(self):
        stopping = Event()
        events = Queue()
        class OneEventHardware:
            calls = 0
            def wait_for_event(self):
                self.calls += 1
                if self.calls == 1:
                    return HallButtonPressed(3, Direction.UP)
                stopping.set()
                return None
        HardwareListener(OneEventHardware(), events, stopping, clock=lambda: 12.5).run()
        self.assertEqual(events.get_nowait().created_at, 12.5)
        self.assertTrue(events.empty())

    def test_stop_before_start_is_supported_and_runtime_is_single_use(self):
        system = ElevatorSystem(FakeHardware(), 10)
        system.stop()
        with self.assertRaises(RuntimeError):
            system.start()


if __name__ == "__main__":
    unittest.main()
