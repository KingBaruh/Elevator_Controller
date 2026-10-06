"""The input worker only forwards messages; it never changes elevator state."""

from dataclasses import replace
from queue import Queue
from threading import Event
from time import monotonic
from typing import Callable

from .events import ControlMessage, HallButtonPressed, HardwareFault
from .hardware import ElevatorHardware


class HardwareListener:
    def __init__(
        self,
        hardware: ElevatorHardware,
        events: Queue[ControlMessage],
        stopping: Event,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.hardware = hardware
        self.events = events
        self.stopping = stopping
        self.clock = clock

    def run(self) -> None:
        try:
            while not self.stopping.is_set():
                event = self.hardware.wait_for_event()
                if self.stopping.is_set():
                    return
                if event is None:
                    continue
                if isinstance(event, HallButtonPressed) and event.created_at is None:
                    event = replace(event, created_at=self.clock())
                self.events.put(event)
        except Exception as error:
            if not self.stopping.is_set():
                self.events.put(HardwareFault(f"Hardware listener failed: {error}"))
