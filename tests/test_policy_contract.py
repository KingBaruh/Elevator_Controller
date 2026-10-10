"""Policy implementations must supply both scheduling decisions."""

import unittest

from elevator_controller import BaselinePolicy, ElevatorPolicy, SchedulingPolicy


class PolicyContractTests(unittest.TestCase):
    def test_abstract_policy_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            ElevatorPolicy()

    def test_both_decisions_are_required(self):
        class PlanningOnly(ElevatorPolicy):
            def plan_next_route(self, elevator, pending, *, now):
                return None

        class UpdatingOnly(ElevatorPolicy):
            def propose_route_update(self, route, elevator, pending, *, now):
                return route

        for policy_type in (PlanningOnly, UpdatingOnly):
            with self.subTest(policy=policy_type.__name__):
                with self.assertRaises(TypeError):
                    policy_type()

    def test_baseline_and_sweep_are_independent_concrete_policies(self):
        baseline = BaselinePolicy()
        sweep = SchedulingPolicy(max_hall_wait=30)
        self.assertIsInstance(baseline, ElevatorPolicy)
        self.assertIsInstance(sweep, ElevatorPolicy)
        self.assertNotIsInstance(baseline, SchedulingPolicy)
        self.assertFalse(hasattr(baseline, "max_hall_wait"))
        self.assertEqual(sweep.max_hall_wait, 30)


if __name__ == "__main__":
    unittest.main()
