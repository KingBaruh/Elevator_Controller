"""Passenger demand shared by both policies, with destinations chosen on boarding."""

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class Passenger:
    passenger_id: str
    ready_at: float
    origin: int
    destinations: tuple[int, ...]
    initially_onboard: bool = False


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int
    duration: float
    passengers: tuple[Passenger, ...]


def scenarios(seeds: tuple[int, ...] = (11, 22, 33, 44, 55)) -> list[Scenario]:
    result = [
        Scenario("direction_change", 0, 60, (
            Passenger("rider", 0, 1, (7, 5), initially_onboard=True),
            Passenger("floor_8", 2, 8, (10,)),
        )),
        Scenario("repeated_cabin", 0, 120, (
            # The same itinerary in both runs; the next button is pressed only
            # after doors open at this rider's current destination.
            Passenger("shuttle", 0, 1, (3, 5) * 30, initially_onboard=True),
            Passenger("floor_9", 1, 9, (10,)),
        )),
    ]
    for seed in seeds:
        random = Random(seed)
        passengers = []
        for index in range(30):
            origin = random.randint(1, 10)
            destination = random.choice([floor for floor in range(1, 11) if floor != origin])
            passengers.append(Passenger(f"passenger_{index:02d}", round(random.uniform(0, 120), 1), origin, (destination,)))
        result.append(Scenario("mixed_traffic", seed, 180, tuple(sorted(passengers, key=lambda p: (p.ready_at, p.passenger_id)))))
    return result
