"""Run a scripted demonstration using two real workers and manual hardware."""

from argparse import ArgumentParser

from . import CabinRequest, Direction, Elevator, ElevatorSystem, HallRequest, PendingRequests, SchedulingPolicy
from .events import ArrivedAtFloor, CabinButtonPressed, DoorsClosed, DoorsOpened, HallButtonPressed
from .fake_hardware import FakeHardware


def policy_demo() -> None:
    requests = PendingRequests(10)
    policy = SchedulingPolicy()
    requests.add_hall(HallRequest(8, Direction.UP, created_at=0))
    route = policy.plan_next_route(Elevator(1), requests, now=0)
    assert route is not None
    print("Committed pickup:", [stop.floor for stop in route.stops])

    requests.add_cabin(CabinRequest(7))
    route = policy.propose_route_update(route, Elevator(1), requests)
    print("Intermediate cabin destination:", [stop.floor for stop in route.stops])

    # Simulate doors opening at 7, completing that stop, and a passenger boarding.
    requests.mark_served(7, route.next_stop.service_direction)
    route = route.after_servicing(7)
    assert route is not None
    requests.add_cabin(CabinRequest(1))
    route = policy.propose_route_update(route, Elevator(7), requests)
    print("New cabin destination 1; keep commitment:", [stop.floor for stop in route.stops])

    requests.mark_served(8, route.next_stop.service_direction)
    assert route.after_servicing(8) is None
    route = policy.plan_next_route(Elevator(8, last_direction=Direction.UP), requests, now=20)
    assert route is not None
    print("Next route after serving 8:", [stop.floor for stop in route.stops])


def threaded_demo() -> None:
    hardware = FakeHardware()
    with ElevatorSystem(hardware, 10, door_dwell_seconds=0.05) as system:
        print("Started workers:", ", ".join(worker.name for worker in system.workers))
        hardware.emit(HallButtonPressed(8, Direction.UP))
        movement = hardware.wait_for_command("move_to_floor")
        print("Hall call at 8 -> movement target:", movement.arguments[0])

        hardware.emit(CabinButtonPressed(7))
        update = hardware.wait_for_command("try_update_target")
        print("Cabin request 7 -> accepted intermediate stop:", update.accepted)

        def serve(floor: int, movement_id: int, after: int) -> int:
            hardware.emit(ArrivedAtFloor(floor, movement_id))
            opening = hardware.wait_for_command("open_doors", after=after)
            hardware.emit(DoorsOpened(opening.arguments[0]))
            print(f"Stopped at {floor}; doors opened.")
            if floor == 7:
                hardware.emit(CabinButtonPressed(1))
                print("Passenger at 7 selects 1; the pickup at 8 stays committed.")
            closing = hardware.wait_for_command("close_doors", after=opening.sequence)
            hardware.emit(DoorsClosed(closing.arguments[0]))
            return closing.sequence

        after = serve(7, movement.arguments[1], update.sequence)
        movement = hardware.wait_for_command("move_to_floor", after=after)
        print("Next movement target:", movement.arguments[0])
        after = serve(8, movement.arguments[1], movement.sequence)
        movement = hardware.wait_for_command("move_to_floor", after=after)
        print("Next movement target:", movement.arguments[0])
        serve(1, movement.arguments[1], movement.sequence)
        final = system.controller.wait_for(lambda state: state.idle and state.elevator.current_floor == 1)
        print("Finished: idle at floor", final.elevator.current_floor)
    print("Both workers stopped.")


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--policy-only", action="store_true", help="Run the original scheduling-only example.")
    args = parser.parse_args()
    if args.policy_only:
        policy_demo()
    else:
        threaded_demo()


if __name__ == "__main__":
    main()
