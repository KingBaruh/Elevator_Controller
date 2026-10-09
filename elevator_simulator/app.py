"""Ten-floor Tkinter view. All widgets and animation run on the main thread."""

import tkinter as tk
from tkinter import messagebox
from time import monotonic

from elevator_controller import Direction, DoorState, ElevatorSystem, MotionState

from .hardware import SimulatorHardware


FLOORS = 10
BG = "#edf1f5"
PAPER = "#ffffff"
INK = "#16283f"
MUTED = "#718095"
LINE = "#e3e9f0"
NAVY = "#172a41"
AMBER = "#ffc665"
GREEN = "#37ac89"
FONT = "Segoe UI"


def ui_font(size: int, bold: bool = False) -> tuple:
    """Pixel-sized fonts keep canvas labels and native buttons aligned at high DPI."""
    return FONT, -round(size * 1.25), "bold" if bold else "normal"


class SimulatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._closed = False
        self._timer: str | None = None
        self._last_activity = -1
        self._hall_active: set[tuple[int, Direction]] = set()
        self._cabin_active: set[int] = set()
        self._drawing_ready = False
        self.hall_buttons: dict[tuple[int, Direction], tk.Button] = {}
        self.cabin_buttons: dict[int, tk.Button] = {}
        self.hardware = SimulatorHardware(FLOORS)
        self.system = ElevatorSystem(self.hardware, FLOORS, door_dwell_seconds=2.0)

        root.title("Elevator Lab | 10-floor simulator")
        root.configure(bg=BG)
        width, height = 1120, 780
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 2 - 24)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.minsize(1000, 720)
        root.option_add("*Font", ui_font(10))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.system.start()
        self._tick()

    @staticmethod
    def _label(parent, text="", *, bg=PAPER, fg=INK, size=10, bold=False, **options):
        return tk.Label(
            parent, text=text, bg=bg, fg=fg,
            font=ui_font(size, bold), **options,
        )

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=(16, 12))
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x", pady=(0, 16))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        self._label(titles, "ELEVATOR LAB  /  INTERACTIVE DEMO", bg=BG, fg=MUTED, size=9, bold=True).pack(anchor="w")
        self._label(titles, "Ten floors. One controller.", bg=BG, size=24, bold=True).pack(anchor="w", pady=(1, 0))
        self._label(titles, "Call from a floor, then choose a destination inside the cabin.", bg=BG, fg=MUTED).pack(anchor="w")
        reset = tk.Button(
            header, text="Reset simulation", command=self.reset, bg=PAPER, fg=INK,
            activebackground=LINE, relief="flat", bd=0, padx=16, pady=9,
            cursor="hand2", highlightthickness=1, highlightbackground=LINE,
        )
        reset.pack(side="right", padx=(12, 0))
        self.status_badge = self._label(header, "●  IDLE", bg="#dcefe8", fg="#24785f", bold=True, padx=14, pady=9)
        self.status_badge.pack(side="right")

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=308)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=PAPER, bd=0, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        self.canvas.bind("<Configure>", self._draw_building)

        for floor_number in range(1, FLOORS + 1):
            for direction, symbol in ((Direction.UP, "▲"), (Direction.DOWN, "▼")):
                if (floor_number == 1 and direction is Direction.DOWN) or (floor_number == FLOORS and direction is Direction.UP):
                    continue
                button = tk.Button(
                    self.canvas, text=symbol, bg="#edf2f6", fg="#586c82",
                    activebackground="#ffe3b0", activeforeground=INK,
                    font=ui_font(12, True), relief="flat", bd=0,
                    cursor="hand2", takefocus=True,
                    command=lambda f=floor_number, d=direction: self.press_hall(f, d),
                )
                self.hall_buttons[floor_number, direction] = button

        sidebar = tk.Frame(body, bg=BG, width=308)
        sidebar.grid(row=0, column=1, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(2, weight=1)

        cabin = tk.Frame(sidebar, bg=NAVY, padx=18, pady=12)
        cabin.grid(row=0, column=0, sticky="ew")
        self._label(cabin, "INSIDE THE CABIN", bg=NAVY, fg="#a8bacd", size=9, bold=True).pack(anchor="w")
        readout = tk.Frame(cabin, bg=NAVY)
        readout.pack(fill="x", pady=(2, 4))
        self.floor_readout = self._label(readout, "01", bg=NAVY, fg="#ffffff", size=36, bold=True)
        self.floor_readout.pack(side="left")
        readout_right = tk.Frame(readout, bg=NAVY)
        readout_right.pack(side="right", anchor="center")
        self.direction_readout = self._label(readout_right, "AT REST", bg=NAVY, fg="#87d9bd", size=10, bold=True)
        self.direction_readout.pack(anchor="e")
        self.door_readout = self._label(readout_right, "Doors closed", bg=NAVY, fg="#a8bacd", size=9)
        self.door_readout.pack(anchor="e", pady=(3, 0))
        self._label(cabin, "SELECT A DESTINATION", bg=NAVY, fg="#a8bacd", size=8, bold=True).pack(anchor="w", pady=(0, 8))
        keypad = tk.Frame(cabin, bg=NAVY)
        keypad.pack(fill="x")
        for column in range(2):
            keypad.columnconfigure(column, weight=1)
        for row, pair in enumerate(((9, 10), (7, 8), (5, 6), (3, 4), (1, 2))):
            for column, floor_number in enumerate(pair):
                button = tk.Button(
                    keypad, text=f"{floor_number:02d}", command=lambda f=floor_number: self.press_cabin(f),
                    bg="#2a4059", fg="#e7eef6", activebackground=AMBER,
                    activeforeground=INK, font=ui_font(13, True), relief="flat", bd=0,
                    cursor="hand2", pady=1, highlightthickness=0, takefocus=True,
                )
                button.grid(row=row, column=column, sticky="ew", padx=(0, 5) if column == 0 else (5, 0), pady=2)
                self.cabin_buttons[floor_number] = button
        self._label(cabin, "Amber buttons = pending destinations", bg=NAVY, fg="#a8bacd", size=8).pack(anchor="w", pady=(6, 0))

        route_card = tk.Frame(sidebar, bg=PAPER, padx=16, pady=10)
        route_card.grid(row=1, column=0, sticky="ew", pady=10)
        self._label(route_card, "CURRENT PLAN", fg=MUTED, size=9, bold=True).pack(anchor="w")
        self.route_readout = self._label(route_card, "No stops queued", size=12, bold=True, anchor="w", wraplength=265)
        self.route_readout.pack(fill="x", pady=(4, 0))
        self.pending_readout = self._label(route_card, "0 hall calls  ·  0 cabin requests", fg=MUTED, size=9)
        self.pending_readout.pack(anchor="w", pady=(4, 0))

        activity_card = tk.Frame(sidebar, bg=PAPER, padx=16, pady=12)
        activity_card.grid(row=2, column=0, sticky="nsew")
        self._label(activity_card, "LIVE ACTIVITY", fg=MUTED, size=9, bold=True).pack(anchor="w", pady=(0, 8))
        self.activity_text = tk.Text(
            activity_card, height=5, width=30, bg=PAPER, fg="#55677e",
            relief="flat", bd=0, highlightthickness=0, wrap="word", state="disabled",
            font=ui_font(9), cursor="arrow", spacing3=5,
        )
        self.activity_text.pack(fill="both", expand=True)

        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x", side="bottom", before=body, pady=(10, 0))
        self.footer_readout = self._label(footer, "Ready at floor 1", bg=BG, fg=MUTED, size=9)
        self.footer_readout.pack(side="left")
        self._label(footer, "10 FLOORS  /  1 LIFT", bg=BG, fg=MUTED, size=9, bold=True).pack(side="right")

    def _draw_building(self, _event=None) -> None:
        canvas = self.canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 100 or height < 100:
            return
        canvas.delete("all")
        self._floor_y: dict[int, float] = {}
        self._wait_displays: dict[tuple[int, Direction], int] = {}
        self._floor_displays: dict[int, int] = {}
        self._top = 74
        self._row_height = (height - self._top - 20) / FLOORS
        # Keep both readout columns clear of the shaft at the minimum window size.
        self._shaft_left, self._shaft_right = max(476, width - 181), width - 37
        canvas.create_text(26, 25, text="BUILDING", anchor="w", fill=INK, font=ui_font(11, True))
        canvas.create_text(42, 45, text="FLOOR", fill=MUTED, font=ui_font(8, True))
        canvas.create_text(190, 45, text="HALL BUTTONS", fill=MUTED, font=ui_font(8, True))
        canvas.create_text(320, 45, text="WAIT TIME", fill=MUTED, font=ui_font(8, True))
        canvas.create_text(430, 45, text="LIFT DISPLAY", fill=MUTED, font=ui_font(8, True))
        for direction, x in ((Direction.UP, 288), (Direction.DOWN, 352)):
            canvas.create_text(x, 61, text="▲" if direction is Direction.UP else "▼", fill=MUTED, font=ui_font(8))
        shaft_center = (self._shaft_left + self._shaft_right) / 2
        canvas.create_text(shaft_center, 35, text="SHAFT  A", fill=MUTED, font=ui_font(9, True))
        canvas.create_rectangle(self._shaft_left, self._top - 6, self._shaft_right, height - 15, fill="#e8eef4", outline="")
        for floor_number in range(FLOORS, 0, -1):
            row = FLOORS - floor_number
            y = self._top + (row + 0.5) * self._row_height
            self._floor_y[floor_number] = y
            bottom = y + self._row_height / 2
            canvas.create_line(22, bottom, width - 22, bottom, fill=LINE)
            canvas.create_text(42, y - 1, text=f"{floor_number:02d}", fill=INK, font=ui_font(19, True))
            canvas.create_text(80, y, text="LOBBY" if floor_number == 1 else "FLOOR", anchor="w", fill=MUTED, font=ui_font(8, True))
            for direction, x in ((Direction.UP, 168), (Direction.DOWN, 220)):
                button = self.hall_buttons.get((floor_number, direction))
                if button is not None:
                    canvas.create_window(x, y, window=button, width=42, height=min(34, self._row_height - 8))
                else:
                    canvas.create_text(x, y, text="—", fill="#b5c1ce", font=ui_font(11))
            for direction, x in ((Direction.UP, 288), (Direction.DOWN, 352)):
                if (floor_number, direction) in self.hall_buttons:
                    canvas.create_rectangle(x - 28, y - 14, x + 28, y + 14, fill="#eaf3ef", outline="")
                    self._wait_displays[floor_number, direction] = canvas.create_text(
                        x, y, text="—", fill=MUTED, font=ui_font(10, True),
                    )
            canvas.create_rectangle(392, y - 14, 468, y + 14, fill=NAVY, outline="")
            self._floor_displays[floor_number] = canvas.create_text(
                430, y, text="01  ·", fill="#87d9bd", font=ui_font(11, True),
            )
        for x in (self._shaft_left + 15, self._shaft_right - 15):
            canvas.create_line(x, self._top, x, height - 20, fill="#c3cedb", width=2)
        self._landing = canvas.create_rectangle(0, 0, 0, 0, fill="#cfebe1", outline="")
        self._target_marker = canvas.create_text(0, 0, text="◀", fill="#d8952b", font=ui_font(12), state="hidden")
        self._car_body = canvas.create_rectangle(0, 0, 0, 0, fill=NAVY, outline="#274b67", width=2)
        self._car_inside = canvas.create_rectangle(0, 0, 0, 0, fill="#9ed9c3", outline="")
        self._car_left_door = canvas.create_rectangle(0, 0, 0, 0, fill="#afc1d1", outline="")
        self._car_right_door = canvas.create_rectangle(0, 0, 0, 0, fill="#94aabd", outline="")
        self._car_label = canvas.create_text(0, 0, text="LIFT A", fill="#e9f6ff", font=ui_font(7, True))
        self._drawing_ready = True
        self._render()

    def _render(self) -> None:
        physical = self.hardware.snapshot
        state = self.system.controller.snapshot
        if physical.motion is MotionState.MOVING:
            phase = "MOVING UP" if physical.direction is Direction.UP else "MOVING DOWN"
        else:
            phase = {
                DoorState.OPENING: "DOORS OPENING", DoorState.OPEN: "BOARDING",
                DoorState.CLOSING: "DOORS CLOSING", DoorState.CLOSED: "IDLE" if state.idle else "PREPARING",
            }[physical.doors]
        if state.fault:
            phase = "FAULT"
        self.status_badge.configure(text=f"●  {phase}", bg="#ffe4df" if state.fault else "#dcefe8", fg="#b44331" if state.fault else "#24785f")
        self.floor_readout.configure(text=f"{physical.display_floor:02d}")
        direction_text = {
            Direction.UP: "▲  UP", Direction.DOWN: "▼  DOWN", None: "AT REST",
        }[physical.display_direction]
        self.direction_readout.configure(text=direction_text)
        self.door_readout.configure(text=f"Doors {physical.doors.value}")
        if state.route is None:
            route_text = f"Serving floor {state.service_stop.floor}" if state.service_stop else "No stops queued"
        else:
            route_text = " → ".join(str(stop.floor) for stop in state.route.stops)
        self.route_readout.configure(text=route_text)
        self.pending_readout.configure(text=f"{len(state.hall_requests)} hall calls  ·  {len(state.cabin_requests)} cabin requests")
        target = f"  ·  Target {physical.target_floor}" if physical.target_floor is not None else ""
        self.footer_readout.configure(
            text=f"Fault: {state.fault}" if state.fault else f"Position {physical.position:.1f}{target}  ·  Amber = pending request",
            fg="#b44331" if state.fault else MUTED,
        )
        hall_active = {(request.floor, request.direction) for request in state.hall_requests}
        cabin_active = {request.floor for request in state.cabin_requests}
        for key, button in self.hall_buttons.items():
            active = key in hall_active
            button.configure(bg=AMBER if active else "#edf2f6", fg=INK if active else "#586c82", state="disabled" if state.fault else "normal")
        for floor_number, button in self.cabin_buttons.items():
            active = floor_number in cabin_active
            button.configure(bg=AMBER if active else "#2a4059", fg=INK if active else "#e7eef6", state="disabled" if state.fault else "normal")
        self._hall_active, self._cabin_active = hall_active, cabin_active

        if physical.activity and physical.activity[-1].sequence != self._last_activity:
            self._last_activity = physical.activity[-1].sequence
            lines = []
            for record in physical.activity[-18:]:
                minutes, seconds = divmod(int(record.elapsed), 60)
                lines.append(f"{minutes:02d}:{seconds:02d}  {record.text}")
            self.activity_text.configure(state="normal")
            self.activity_text.delete("1.0", "end")
            self.activity_text.insert("end", "\n".join(lines))
            self.activity_text.see("end")
            self.activity_text.configure(state="disabled")

        if not self._drawing_ready:
            return
        canvas = self.canvas
        symbol = {Direction.UP: "↑", Direction.DOWN: "↓", None: "·"}[physical.display_direction]
        for item in self._floor_displays.values():
            canvas.itemconfigure(item, text=f"{physical.display_floor:02d}  {symbol}")
        now = monotonic()
        waiting_since = {(request.floor, request.direction): request.created_at for request in state.hall_requests}
        for key, item in self._wait_displays.items():
            started_at = waiting_since.get(key)
            if started_at is None:
                canvas.itemconfigure(item, text="—", fill=MUTED)
            else:
                minutes, seconds = divmod(max(0, int(now - started_at)), 60)
                canvas.itemconfigure(item, text=f"{minutes:02d}:{seconds:02d}", fill="#377c65")
        y = self._floor_y[1] - (physical.position - 1) * self._row_height
        center = (self._shaft_left + self._shaft_right) / 2
        height = min(46, self._row_height * 0.78)
        left, right, top, bottom = center - 47, center + 47, y - height / 2, y + height / 2
        canvas.coords(self._landing, self._shaft_left + 2, y - self._row_height / 2 + 2, self._shaft_right - 2, y + self._row_height / 2 - 2)
        canvas.itemconfigure(self._landing, state="normal" if physical.motion is MotionState.STOPPED else "hidden")
        canvas.coords(self._car_body, left, top, right, bottom)
        inside_left, inside_right = left + 6, right - 6
        canvas.coords(self._car_inside, inside_left, top + 12, inside_right, bottom - 4)
        door_width = (inside_right - inside_left) / 2 * (1 - physical.door_open_fraction)
        canvas.coords(self._car_left_door, inside_left, top + 12, inside_left + door_width, bottom - 4)
        canvas.coords(self._car_right_door, inside_right - door_width, top + 12, inside_right, bottom - 4)
        canvas.coords(self._car_label, center, top + 6)
        if physical.target_floor is not None:
            canvas.coords(self._target_marker, self._shaft_right + 12, self._floor_y[physical.target_floor])
            canvas.itemconfigure(self._target_marker, state="normal")
        else:
            canvas.itemconfigure(self._target_marker, state="hidden")

    def press_hall(self, floor_number: int, direction: Direction) -> None:
        self.hardware.press_hall(floor_number, direction)

    def press_cabin(self, floor_number: int) -> None:
        self.hardware.press_cabin(floor_number)

    def _tick(self) -> None:
        if self._closed:
            return
        self.hardware.tick()
        self._render()
        self._timer = self.root.after(25, self._tick)

    def reset(self) -> None:
        self.hardware.shutdown()
        self.system.stop()
        self.hardware = SimulatorHardware(FLOORS)
        self.system = ElevatorSystem(self.hardware, FLOORS, door_dwell_seconds=2.0)
        self._last_activity = -1
        self.system.start()
        self._render()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._timer is not None:
            self.root.after_cancel(self._timer)
        self.hardware.shutdown()
        try:
            self.system.stop()
        except TimeoutError as error:
            messagebox.showerror("Shutdown error", str(error), parent=self.root)
        finally:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    SimulatorApp(root)
    root.mainloop()
