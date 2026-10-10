"""Run the real controller with virtual time, without worker threads or Tkinter."""

from dataclasses import dataclass
from math import isfinite
from queue import Empty
from statistics import mean

from elevator_controller import Direction, DoorState, ElevatorController, SchedulingPolicy
from elevator_controller.events import CabinButtonPressed, DoorsOpened, HallButtonPressed
from elevator_simulator.hardware import SimulatorHardware

from .metrics import Metrics
from .scenarios import Passenger, Scenario


@dataclass(frozen=True)
class Settings:
    time_step: float = 0.1
    seconds_per_floor: float = 1.0
    door_seconds: float = 0.5
    door_dwell_seconds: float = 2.0

    def __post_init__(self):
        for value in (self.time_step, self.seconds_per_floor, self.door_seconds, self.door_dwell_seconds):
            if not isfinite(value) or value <= 0:
                raise ValueError("Simulation settings must be finite and positive.")


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class ExperimentHardware(SimulatorHardware):
    def drain_events(self):
        """Replace only the blocking reader for a single-threaded experiment.

        This adapter uses the simulator's existing event queue; physics, command
        acceptance and event generation are inherited unchanged.
        """
        while True:
            try:
                yield self._events.get_nowait()
            except Empty:
                return


@dataclass
class Rider:
    passenger: Passenger
    status: str = "future"
    leg: int = 0
    boarded_at: float | None = None
    completed_at: float | None = None

    @property
    def destination(self) -> int:
        return self.passenger.destinations[self.leg]


@dataclass
class Result:
    scenario: Scenario
    policy_name: str
    summary: dict
    metrics: Metrics
    trace: list[dict]
    services: list[dict]
    passengers: list[dict]


def run(scenario: Scenario, policy: SchedulingPolicy, policy_name: str, settings: Settings = Settings()) -> Result:
    if not isfinite(scenario.duration) or scenario.duration <= 0:
        raise ValueError("Time step and duration must be positive.")
    steps = round(scenario.duration / settings.time_step)
    if abs(steps * settings.time_step - scenario.duration) > 1e-8:
        raise ValueError("Duration must be a multiple of the time step.")
    clock = Clock()
    hardware = ExperimentHardware(
        seconds_per_floor=settings.seconds_per_floor, door_seconds=settings.door_seconds, clock=clock,
    )
    controller = ElevatorController(
        hardware, 10, policy=policy, clock=clock, door_dwell_seconds=settings.door_dwell_seconds,
    )
    riders = [Rider(passenger) for passenger in scenario.passengers]
    metrics = Metrics()
    trace, services = [], []

    def drain():
        for event in hardware.drain_events():
            if isinstance(event, HallButtonPressed):
                metrics.press("hall", event.floor, event.direction, clock.now)
            elif isinstance(event, CabinButtonPressed):
                metrics.press("cabin", event.floor, None, clock.now)
            controller.handle_event(event)
            state = controller.snapshot
            if state.fault or state.rejected_input:
                raise RuntimeError(f"{scenario.name}/{policy_name} at {clock.now}: {state.fault or state.rejected_input}")
            metrics.observe(state, clock.now)
            if isinstance(event, DoorsOpened):
                services.append({"time_s": clock.now, "floor": state.elevator.current_floor,
                                 "direction": state.service_stop.service_direction.value if state.service_stop.service_direction else ""})

    def press_destination(rider):
        hardware.press_cabin(rider.destination)
        drain()

    distance = 0.0
    previous_position = 1.0
    for step in range(steps + 1):
        clock.now = round(step * settings.time_step, 10)
        hardware.tick()
        drain()
        for rider in riders:
            p = rider.passenger
            if rider.status == "future" and p.ready_at <= clock.now + 1e-9:
                if p.initially_onboard:
                    if p.ready_at != 0 or p.origin != 1:
                        raise ValueError("Initially onboard riders must start at floor 1 at time zero.")
                    rider.status, rider.boarded_at = "riding", clock.now
                    press_destination(rider)
                else:
                    rider.status = "waiting"
                    direction = Direction.UP if rider.destination > p.origin else Direction.DOWN
                    hardware.press_hall(p.origin, direction)
                    drain()

        # Boarding and next itinerary legs depend on actual door/service state.
        # External release times remain the same under both policies.
        for rider in riders:
            state = controller.snapshot
            if state.elevator.doors is not DoorState.OPEN:
                break
            floor = state.elevator.current_floor
            if rider.status == "waiting" and rider.passenger.origin == floor:
                direction = Direction.UP if rider.destination > floor else Direction.DOWN
                if state.service_stop.service_direction is direction:
                    rider.status, rider.boarded_at = "riding", clock.now
                    press_destination(rider)
            elif rider.status == "riding" and rider.destination == floor:
                rider.leg += 1
                if rider.leg == len(rider.passenger.destinations):
                    rider.status, rider.completed_at = "done", clock.now
                else:
                    press_destination(rider)

        controller.check_timeouts()
        if controller.snapshot.fault:
            raise RuntimeError(controller.snapshot.fault)
        position = hardware.snapshot.position
        distance += abs(position - previous_position)
        previous_position = position
        trace.append({
            "time_s": clock.now, "position": position,
            "oldest_hall_wait_s": metrics.oldest_hall_age(clock.now),
            "hall_pending": len(controller.snapshot.hall_requests),
            "cabin_pending": len(controller.snapshot.cabin_requests),
        })

    passenger_waits = [r.boarded_at - r.passenger.ready_at for r in riders
                       if r.boarded_at is not None and not r.passenger.initially_onboard]
    summary = {"scenario": scenario.name, "seed": scenario.seed, "policy": policy_name,
               "duration_s": scenario.duration, **metrics.summary(clock.now), "distance_floors": distance,
               "passengers_released": sum(r.status != "future" for r in riders),
               "passenger_mean_wait_s": mean(passenger_waits) if passenger_waits else None,
               "passenger_max_wait_s": max(passenger_waits) if passenger_waits else None,
               "passengers_completed": sum(r.status == "done" for r in riders),
               "passengers_waiting": sum(r.status == "waiting" for r in riders),
               "passengers_riding": sum(r.status == "riding" for r in riders)}
    passenger_rows = [{"passenger_id": r.passenger.passenger_id, "status": r.status,
                       "ready_at": r.passenger.ready_at, "boarded_at": r.boarded_at,
                       "completed_at": r.completed_at, "legs_completed": r.leg} for r in riders]
    hardware.shutdown()
    return Result(scenario, policy_name, summary, metrics, trace, services, passenger_rows)
