"""The hardware base cannot be instantiated without concrete operations."""

import unittest

from elevator_controller.hardware import ElevatorHardware
from elevator_controller.fake_hardware import FakeHardware
from elevator_controller.models import Direction, Elevator
from elevator_simulator.hardware import SimulatorHardware


class HardwareContractTests(unittest.TestCase):
    def test_abstract_base_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            ElevatorHardware()

    def test_partial_adapter_cannot_be_instantiated(self):
        class PartialHardware(ElevatorHardware):
            def get_initial_state(self):
                return Elevator(1)

        with self.assertRaises(TypeError):
            PartialHardware()

    def test_both_adapters_support_the_same_keyword_commands(self):
        # Call through the base contract, including parameter names. A fixed
        # clock keeps the simulator at its start while these commands are sent.
        for hardware in (FakeHardware(), SimulatorHardware(clock=lambda: 0.0)):
            with self.subTest(adapter=type(hardware).__name__):
                self.assertIsInstance(hardware, ElevatorHardware)
                self.assertTrue(hardware.move_to_floor(floor=3, command_id=1))
                self.assertTrue(hardware.try_update_target(floor=5, command_id=1))
                hardware.update_displays(floor=1, direction=Direction.UP)
                self.assertEqual(hardware.get_initial_state().target_floor, 5)


if __name__ == "__main__":
    unittest.main()
