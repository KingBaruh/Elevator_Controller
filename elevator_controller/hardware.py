"""The hardware contract. No motor or device-specific implementation is needed."""

from typing import Protocol

from .events import HardwareEvent
from .models import Direction, Elevator


class ElevatorHardware(Protocol):
    """One listener may read events concurrently with controller commands.

    Commands must return promptly. True means accepted, not completed. Accepted
    operations report completion with the supplied command_id; IDs are unique
    within one controller session. Events are buffered and delivered in order.

    A retarget and an arrival must be serialized by the adapter: once an arrival
    is committed/queued, retargeting that movement must return False. A rejected
    retarget leaves the original target intact. The adapter determines braking
    feasibility and enforces the physical door/motion interlocks.

    The exercise starts stationary at a known floor with closed doors. Reusing
    an adapter for a new session requires clearing events from the old session.
    """

    def get_initial_state(self) -> Elevator:
        """Return the elevator state at the start of a new controller session."""
        ...

    def wait_for_event(self) -> HardwareEvent | None:
        """Block until an event, or return None when cancel_wait wakes the reader."""
        ...

    def cancel_wait(self) -> None:
        """Wake the reader, even if called immediately before it starts waiting."""
        ...

    def move_to_floor(self, floor: int, command_id: int) -> bool:
        """Request a new target floor. Return True if accepted, False if rejected."""
        ...

    def try_update_target(self, floor: int, command_id: int) -> bool:
        """Shorten or extend the active movement without reversing.

        Extending a pickup approach is a controller decision. The adapter may
        reject the change if braking for the old target can no longer be canceled.
        """
        ...

    def open_doors(self, command_id: int) -> bool:
        """Request the doors to open. Return True if accepted, False if rejected."""
        ...

    def close_doors(self, command_id: int) -> bool:
        """Request the doors to close. Return True if accepted, False if rejected."""
        ...

    def update_displays(self, floor: int, direction: Direction | None) -> None:
        """Update the floor and direction displays. Called after each state change."""
        ...
