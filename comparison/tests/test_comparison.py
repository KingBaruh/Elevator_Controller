from dataclasses import asdict
import unittest

from elevator_controller import BaselinePolicy, Direction, SchedulingPolicy
from elevator_controller.controller import ControllerSnapshot
from elevator_controller.models import Elevator, HallRequest

from comparison.metrics import Metrics
from comparison.runner import run
from comparison.scenarios import Passenger, Scenario, scenarios


class ComparisonTests(unittest.TestCase):
    def test_direction_scenario_has_the_expected_actual_stop_sequences(self):
        scenario = scenarios()[0]
        baseline = run(scenario, BaselinePolicy(), "baseline")
        sweep = run(scenario, SchedulingPolicy(), "service_sweep")
        self.assertEqual([s["floor"] for s in baseline.services], [7, 5, 8, 10])
        self.assertEqual([s["floor"] for s in sweep.services], [7, 8, 10, 5])
        for result in (baseline, sweep):
            self.assertEqual(result.summary["hall_pending"], 0)
            self.assertEqual(result.summary["passengers_completed"], 2)

    def test_repeated_cabin_reports_unserved_hall_request_instead_of_zero_wait(self):
        scenario = scenarios()[1]
        baseline = run(scenario, BaselinePolicy(), "baseline")
        sweep = run(scenario, SchedulingPolicy(), "service_sweep")
        self.assertEqual(baseline.summary["hall_completed"], 0)
        self.assertEqual(baseline.summary["hall_pending"], 1)
        self.assertIsNone(baseline.summary["hall_mean_wait_s"])
        self.assertEqual(baseline.summary["hall_oldest_pending_s"], 119)
        self.assertEqual(sweep.summary["hall_completed"], 1)
        self.assertEqual(sweep.summary["hall_pending"], 0)

    def test_same_seed_reproduces_workload_and_run(self):
        first, second = scenarios((11,))[-1], scenarios((11,))[-1]
        self.assertEqual(first, second)
        a = run(first, SchedulingPolicy(), "service_sweep")
        b = run(second, SchedulingPolicy(), "service_sweep")
        self.assertEqual(a.summary, b.summary)
        self.assertEqual(a.trace, b.trace)
        self.assertEqual([asdict(r) for r in a.metrics.records], [asdict(r) for r in b.metrics.records])
        for row in a.passengers:
            if row["boarded_at"] is not None:
                self.assertGreaterEqual(row["boarded_at"], row["ready_at"])

    def test_simultaneous_passengers_share_a_call_but_all_board(self):
        scenario = Scenario("duplicates", 0, 40, (
            Passenger("a", 0, 3, (7,)), Passenger("b", 0, 3, (7,)),
        ))
        result = run(scenario, SchedulingPolicy(), "service_sweep")
        self.assertEqual(result.summary["hall_requested"], 1)
        self.assertEqual(result.summary["cabin_requested"], 1)
        self.assertEqual(result.summary["passengers_completed"], 2)

    def test_call_during_open_doors_can_have_zero_wait(self):
        scenario = Scenario("open_doors", 0, 30, (
            Passenger("a", 0, 3, (7,)), Passenger("b", 3, 3, (7,)),
        ))
        result = run(scenario, SchedulingPolicy(), "service_sweep")
        calls = [r for r in result.metrics.records if r.kind == "hall"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].served_at, calls[1].created_at)

    def test_metrics_keep_first_press_and_exclude_pending_from_completed_mean(self):
        metrics = Metrics()
        metrics.press("hall", 3, Direction.UP, 0)
        metrics.press("hall", 3, Direction.UP, 5)
        metrics.press("hall", 5, Direction.DOWN, 2)
        snapshot = ControllerSnapshot(Elevator(3), None, None, (HallRequest(5, Direction.DOWN, 2),), (), None, None)
        metrics.observe(snapshot, 10)
        summary = metrics.summary(20)
        self.assertEqual(summary["hall_requested"], 2)
        self.assertEqual(summary["hall_completed"], 1)
        self.assertEqual(summary["hall_mean_wait_s"], 10)
        self.assertEqual(summary["hall_pending"], 1)
        self.assertEqual(summary["hall_oldest_pending_s"], 18)
