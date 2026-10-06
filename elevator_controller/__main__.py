"""A small, deterministic walkthrough; no hardware or worker threads."""

from . import CabinRequest, Direction, Elevator, HallRequest, PendingRequests, SchedulingPolicy


def main() -> None:
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


if __name__ == "__main__":
    main()
