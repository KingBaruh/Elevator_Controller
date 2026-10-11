"""A small group-aware wrapper around the unchanged single-car controller."""

from elevator_controller import DoorState, ElevatorController, PendingRequests
from elevator_controller.events import CabinButtonPressed, HallButtonPressed

from .messages import (
    AssignHallCall, AssignmentAck, AssignmentsReleased, CarReport, CarStatus,
    HallCallServed, ReleaseHallCalls,
)


class GroupCarController(ElevatorController):
    """The car worker owns both local requests and assignment tokens.

    Extends only the input/publication boundaries. Movement, doors, deadlines
    and route planning remain in ElevatorController. Release is permitted only
    after the controller has latched a fault and will issue no further commands.
    """

    def __init__(self, elevator_id, hardware, number_of_floors, reports, **options):
        super().__init__(hardware, number_of_floors, **options)
        self.elevator_id = elevator_id
        self._reports = reports
        self._assignments = {}
        self._handled_cabin = None

    def status(self):
        physical = self._hardware.snapshot
        return CarStatus(self.elevator_id, self.snapshot, self._clock(), physical.position,
                         physical.door_open_fraction, self._dwell_deadline)

    def _publish(self):
        super()._publish()
        state = self.snapshot
        if not state.fault and state.elevator.doors is DoorState.OPEN and state.service_stop:
            pending = {(r.floor, r.direction) for r in state.hall_requests}
            for token, call in tuple(self._assignments.items()):
                request = call.request
                if (request.floor == state.elevator.current_floor
                        and request.direction is state.service_stop.service_direction
                        and (request.floor, request.direction) not in pending):
                    self._reports.put(HallCallServed(self.elevator_id, *token, self._clock()))
                    del self._assignments[token]
        self._reports.put(CarReport(self.status(), self._handled_cabin))

    def handle_event(self, event):
        if isinstance(event, AssignHallCall):
            self._assign(event.call)
        elif isinstance(event, ReleaseHallCalls):
            self._release()
        else:
            self._handled_cabin = event.floor if isinstance(event, CabinButtonPressed) else None
            try:
                super().handle_event(event)
            finally:
                self._handled_cabin = None

    def _assign(self, call):
        token = (call.request_id, call.assignment_id)
        if self.snapshot.fault:
            self._reports.put(AssignmentAck(self.elevator_id, *token, False))
            return
        self._assignments[token] = call
        request = call.request
        super().handle_event(HallButtonPressed(request.floor, request.direction, request.created_at))
        # A command failure may latch a fault AFTER the request was registered.
        # Such a request remains owned until the dispatcher receives release.
        accepted = request in self.snapshot.hall_requests or token not in self._assignments
        if not accepted:
            self._assignments.pop(token, None)
        self._reports.put(AssignmentAck(self.elevator_id, *token, accepted))

    def _release(self):
        if not self.snapshot.fault:
            return  # A healthy car must not silently abandon its promises.
        released = tuple(self._assignments)
        self._assignments.clear()
        # Preserve cabin requests: their passengers belong to this car.
        pending = PendingRequests(self._pending.number_of_floors)
        for request in self._pending.cabin:
            pending.add_cabin(request)
        self._pending = pending
        self._route = None
        self._publish()
        self._reports.put(AssignmentsReleased(self.elevator_id, released))
