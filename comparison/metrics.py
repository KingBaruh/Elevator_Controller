"""Request-level metrics. Completed waits and still-pending ages stay separate."""

from dataclasses import dataclass
from statistics import mean

from elevator_controller import ControllerSnapshot, Direction


@dataclass
class RequestRecord:
    request_id: int
    kind: str
    floor: int
    direction: str
    created_at: float
    served_at: float | None = None


class Metrics:
    def __init__(self) -> None:
        self.records: list[RequestRecord] = []
        self.active: dict[tuple[str, int, str], RequestRecord] = {}

    def press(self, kind: str, floor: int, direction: Direction | None, now: float) -> None:
        key = kind, floor, direction.value if direction else ""
        if key not in self.active:
            record = RequestRecord(len(self.records) + 1, *key, now)
            self.records.append(record)
            self.active[key] = record

    def observe(self, snapshot: ControllerSnapshot, now: float) -> None:
        pending = {("hall", r.floor, r.direction.value) for r in snapshot.hall_requests}
        pending.update(("cabin", r.floor, "") for r in snapshot.cabin_requests)
        for key in list(self.active):
            if key not in pending:
                # Called after each event, not between queuing and handling a press.
                self.active.pop(key).served_at = now

    def oldest_hall_age(self, now: float) -> float:
        return max((now - r.created_at for r in self.active.values() if r.kind == "hall"), default=0.0)

    def summary(self, now: float) -> dict:
        result = {}
        for kind in ("hall", "cabin"):
            records = [r for r in self.records if r.kind == kind]
            waits = [r.served_at - r.created_at for r in records if r.served_at is not None]
            pending = [r for r in records if r.served_at is None]
            result.update({
                f"{kind}_requested": len(records),
                f"{kind}_completed": len(waits),
                f"{kind}_pending": len(pending),
                f"{kind}_mean_wait_s": mean(waits) if waits else None,
                f"{kind}_max_wait_s": max(waits) if waits else None,
                f"{kind}_oldest_pending_s": max((now - r.created_at for r in pending), default=0.0),
            })
        return result
