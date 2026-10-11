"""Single owner of shared hall calls; routes messages without driving motors."""

from collections import deque
from dataclasses import replace
from queue import Empty
from threading import Condition
from time import monotonic

from elevator_controller import Direction, HallRequest, PendingRequests
from elevator_controller.events import CabinButtonPressed, HallButtonPressed, Shutdown
from elevator_controller.models import validate_time

from .messages import (
    AssignHallCall, AssignmentAck, AssignmentsReleased, CarEvent, CarReport,
    GroupActivity, GroupFault, GroupSnapshot, HallCall, HallCallServed,
    ReleaseHallCalls, ServiceRecord,
)


class ElevatorDispatcher:
    def __init__(self, number_of_floors, car_queues, statuses, policies, dispatch_policy,
                 *, clock=monotonic, assignment_timeout=2.0):
        validate_time(assignment_timeout)
        if assignment_timeout <= 0:
            raise ValueError("Assignment timeout must be positive.")
        self.number_of_floors = number_of_floors
        self._queues = car_queues
        self._cars = {status.elevator_id: status for status in statuses}
        self._policies = policies
        self._policy = dispatch_policy
        self._clock = clock
        self._assignment_timeout = assignment_timeout
        self._calls = {}
        self._pending_cabins = {car_id: set() for car_id in car_queues}
        self._releasing = set()
        self._next_request = self._next_assignment = 1
        self._activity = deque(maxlen=100)
        self._services = deque(maxlen=100)
        self._completed = 0
        self._error = None
        self._changed = Condition()
        self._sequence = 0
        self._record("Two elevators ready. Shared hall calls; separate cabin controls.")
        self._snapshot = self._make_snapshot()

    def _make_snapshot(self):
        return GroupSnapshot(tuple(self._calls.values()), tuple(self._cars[key] for key in sorted(self._cars)),
                             tuple(self._activity), tuple(self._services), self._completed, self._error)

    @property
    def snapshot(self):
        with self._changed:
            return self._snapshot

    def wait_for(self, predicate, timeout=2.0):
        with self._changed:
            if not self._changed.wait_for(lambda: predicate(self._snapshot), timeout):
                raise TimeoutError("Requested group state was not reached.")
            return self._snapshot

    def _publish(self):
        with self._changed:
            self._snapshot = self._make_snapshot()
            self._changed.notify_all()

    def _record(self, text):
        self._sequence += 1
        self._activity.append(GroupActivity(self._sequence, self._clock(), text))

    def _matching_call(self, message):
        return next((call for call in self._calls.values()
                     if call.request_id == message.request_id
                     and call.assignment_id == message.assignment_id
                     and call.elevator_id == message.elevator_id), None)

    @staticmethod
    def _key(call):
        return call.request.floor, call.request.direction

    def handle_event(self, message):
        if isinstance(message, HallButtonPressed):
            self._register(message)
        elif isinstance(message, CarEvent):
            if message.elevator_id not in self._queues:
                raise ValueError("Event has an unknown elevator ID.")
            # Hall buttons belong to the building, never to an individual car.
            if isinstance(message.event, HallButtonPressed):
                raise ValueError("Hall calls must enter the shared dispatcher.")
            if isinstance(message.event, CabinButtonPressed):
                self._pending_cabins[message.elevator_id].add(message.event.floor)
                self._record(f"Cabin {message.elevator_id} selected floor {message.event.floor}.")
            self._queues[message.elevator_id].put(message.event)
        elif isinstance(message, CarReport):
            car_id = message.status.elevator_id
            previous = self._cars[car_id].controller
            self._cars[car_id] = message.status
            current = message.status.controller
            if current.fault and not previous.fault:
                self._record(f"Lift {car_id} unavailable: {current.fault}")
            elif current.elevator.target_floor != previous.elevator.target_floor and current.elevator.target_floor:
                self._record(f"Lift {car_id} heading to floor {current.elevator.target_floor}.")
            if message.handled_cabin is not None:
                self._pending_cabins[car_id].discard(message.handled_cabin)
            if message.status.controller.fault:
                self._request_release(car_id)
        elif isinstance(message, AssignmentAck):
            call = self._matching_call(message)
            if call is not None:
                if message.accepted:
                    if call.elevator_id not in self._releasing:
                        self._calls[self._key(call)] = replace(call, state="assigned")
                else:
                    self._calls[self._key(call)] = replace(call, elevator_id=None, assignment_id=None,
                                                          state="waiting", sent_at=None)
                    self._record(f"Lift {message.elevator_id} rejected call #{call.request_id}.")
        elif isinstance(message, HallCallServed):
            call = self._matching_call(message)
            if call is not None:
                wait = max(0, message.served_at - call.request.created_at)
                self._services.append(ServiceRecord(message.elevator_id, call.request_id, call.request.floor,
                                                    call.request.direction.value, wait))
                self._completed += 1
                del self._calls[self._key(call)]
                self._record(f"Lift {message.elevator_id} served floor {call.request.floor} "
                             f"{call.request.direction.value}; waited {wait:.1f}s.")
        elif isinstance(message, AssignmentsReleased):
            self._release_confirmed(message)
        elif isinstance(message, GroupFault):
            self._error = message.details
            self._record(f"Group error: {message.details}")
        else:
            raise ValueError(f"Unknown group message: {message!r}")
        self._dispatch_waiting()
        self._publish()

    def _register(self, message):
        created_at = self._clock() if message.created_at is None else message.created_at
        request = HallRequest(message.floor, message.direction, created_at)
        if created_at > self._clock():
            raise ValueError("Hall call timestamp is in the future.")
        # Use the same floor/direction validation as the original controller.
        PendingRequests(self.number_of_floors).add_hall(request)
        key = request.floor, request.direction
        if key in self._calls:
            return
        call = HallCall(self._next_request, request)
        self._next_request += 1
        self._calls[key] = call
        self._record(f"Floor {request.floor} called {request.direction.value} (#{call.request_id}).")

    def _dispatch_waiting(self):
        if self._error:
            return
        waiting = sorted((call for call in self._calls.values() if call.state == "waiting"),
                         key=lambda call: (call.request.created_at, call.request_id))
        for call in waiting:
            car_id = self._policy.choose(call.request, tuple(self._cars.values()), tuple(self._calls.values()),
                                         self._pending_cabins, self._policies, now=self._clock())
            if car_id is None:
                continue
            assignment = replace(call, elevator_id=car_id, assignment_id=self._next_assignment,
                                 state="assigning", sent_at=self._clock())
            self._next_assignment += 1
            # Reserve BEFORE sending; another call sees this in-flight workload.
            self._calls[self._key(call)] = assignment
            self._queues[car_id].put(AssignHallCall(assignment))
            self._record(f"Call #{call.request_id} assigned to lift {car_id}.")

    def _request_release(self, car_id):
        owned = [call for call in self._calls.values() if call.elevator_id == car_id]
        if not owned or car_id in self._releasing:
            return
        self._releasing.add(car_id)
        for call in owned:
            self._calls[self._key(call)] = replace(call, state="releasing", sent_at=self._clock())
        self._queues[car_id].put(ReleaseHallCalls())
        self._record(f"Lift {car_id} faulted; waiting for hall-call release confirmation.")

    def _release_confirmed(self, message):
        car_id = message.elevator_id
        if car_id not in self._releasing or not self._cars[car_id].controller.fault:
            return
        for request_id, assignment_id in message.assignments:
            call = self._matching_call(AssignmentAck(car_id, request_id, assignment_id, True))
            if call is not None:
                self._calls[self._key(call)] = replace(call, elevator_id=None, assignment_id=None,
                                                      state="waiting", sent_at=None)
                self._record(f"Call #{request_id} released by lift {car_id}; original wait time retained.")
        self._releasing.discard(car_id)

    def check_timeouts(self):
        changed = False
        for key, call in tuple(self._calls.items()):
            if call.state in ("assigning", "releasing") and self._clock() - call.sent_at >= self._assignment_timeout:
                self._calls[key] = replace(call, state="uncertain")
                self._record(f"Call #{call.request_id}: {call.state} acknowledgment overdue; ownership retained.")
                changed = True
        if changed:
            self._publish()

    def run(self, events, stopping):
        while not stopping.is_set():
            try:
                message = events.get(timeout=0.1)
            except Empty:
                self.check_timeouts()
                continue
            try:
                if isinstance(message, Shutdown) or stopping.is_set():
                    return
                try:
                    self.handle_event(message)
                except Exception as error:
                    self._error = f"Dispatcher failed: {error}"
                    self._record(self._error)
                    self._publish()
                self.check_timeouts()
            finally:
                events.task_done()
