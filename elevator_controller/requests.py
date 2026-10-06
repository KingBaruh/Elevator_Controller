"""Pending requests owned exclusively by the controller thread."""

from .models import CabinRequest, Direction, HallRequest, validate_floor


class PendingRequests:
    """Deduplicated requests, separate from the thread-safe hardware event queue.

    This collection is intentionally not shared between threads. The listener
    will send events; only the controller will call its mutation methods.
    """

    def __init__(self, number_of_floors: int) -> None:
        validate_floor(number_of_floors)
        self.number_of_floors = number_of_floors
        self._hall: dict[tuple[int, Direction], HallRequest] = {}
        self._cabin: dict[int, CabinRequest] = {}

    def _check_floor(self, floor: int) -> None:
        validate_floor(floor)
        if floor > self.number_of_floors:
            raise ValueError("Floor is outside the building.")

    def add_hall(self, request: HallRequest) -> bool:
        """Return False for a duplicate; retain the first acceptance time."""
        self._check_floor(request.floor)
        if request.floor == 1 and request.direction is Direction.DOWN:
            raise ValueError("There is no DOWN hall button on the bottom floor.")
        if request.floor == self.number_of_floors and request.direction is Direction.UP:
            raise ValueError("There is no UP hall button on the top floor.")
        key = (request.floor, request.direction)
        if key in self._hall:
            return False
        self._hall[key] = request
        return True

    def add_cabin(self, request: CabinRequest) -> bool:
        self._check_floor(request.floor)
        if request.floor in self._cabin:
            return False
        self._cabin[request.floor] = request
        return True

    @property
    def hall(self) -> tuple[HallRequest, ...]:
        return tuple(self._hall.values())

    @property
    def cabin(self) -> tuple[CabinRequest, ...]:
        return tuple(self._cabin.values())

    def __bool__(self) -> bool:
        return bool(self._hall or self._cabin)

    def mark_served(self, floor: int, service_direction: Direction | None) -> None:
        """Call only after doors open at this floor with this service direction.

        Arriving, passing a floor, or accepting a command does not fulfill a
        request. The opposite hall direction always remains pending.
        """
        self._check_floor(floor)
        self._cabin.pop(floor, None)
        if service_direction is not None:
            self._hall.pop((floor, service_direction), None)
