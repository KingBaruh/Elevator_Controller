"""Ten-floor, two-shaft Tkinter view; widgets and animation stay on the main thread."""

import argparse
import tkinter as tk
from tkinter import messagebox

from elevator_controller import BaselinePolicy, Direction, DoorState, MotionState, SchedulingPolicy

from .system import ElevatorGroupSystem


FLOORS = 10
BG, PAPER, INK, MUTED, LINE = "#edf1f5", "#ffffff", "#16283f", "#718095", "#e3e9f0"
NAVY, AMBER = "#172a41", "#ffc665"
CAR_COLORS = {"A": "#247ba0", "B": "#7d65aa"}
CAR_PALE = {"A": "#e1f0f5", "B": "#eee8f7"}


def font(size, bold=False):
    return "Segoe UI", -round(size * 1.25), "bold" if bold else "normal"


class GroupSimulatorApp:
    def __init__(self, root, *, policy_factory=SchedulingPolicy, **simulation_options):
        self.root = root
        self.policy_factory = policy_factory
        self.simulation_options = simulation_options
        self._closed = False
        self._timer = None
        self._drawing_ready = False
        self._last_activity = -1
        self.hall_buttons, self.cabin_buttons = {}, {}
        self.car_widgets = {}
        self._new_system()
        root.title("Elevator Lab | Two elevators | " + (
            "Baseline" if policy_factory is BaselinePolicy else "Service sweep + aging"))
        root.configure(bg=BG)
        width = min(1380, root.winfo_screenwidth() - 80)
        height = min(850, root.winfo_screenheight() - 100)
        root.geometry(f"{width}x{height}+30+30")
        root.minsize(1160, 720)
        root.option_add("*Font", font(10))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.system.start()
        self._tick()

    def _new_system(self):
        self.system = ElevatorGroupSystem(FLOORS, policy_factory=self.policy_factory, **self.simulation_options)
        self._started_at = self.system.clock()

    @staticmethod
    def _label(parent, text="", *, bg=PAPER, fg=INK, size=10, bold=False, **options):
        return tk.Label(parent, text=text, bg=bg, fg=fg, font=font(size, bold), **options)

    @staticmethod
    def _button(parent, text, command, **options):
        return tk.Button(parent, text=text, command=command, relief="flat", bd=0, cursor="hand2",
                         bg=PAPER, fg=INK, activebackground=AMBER, padx=12, pady=8, **options)

    def _build(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=22, pady=16)
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x", pady=(0, 12))
        title = tk.Frame(header, bg=BG)
        title.pack(side="left")
        self._label(title, "ELEVATOR LAB  /  TWO-CAR GROUP", bg=BG, fg=MUTED, size=9, bold=True).pack(anchor="w")
        self._label(title, "Two elevators. One shared call.", bg=BG, size=24, bold=True).pack(anchor="w")
        self._label(title, "Call from a floor. Board the assigned lift. Choose its cabin destination.",
                    bg=BG, fg=MUTED, size=9).pack(anchor="w", pady=(3, 0))
        self._button(header, "Reset simulation", self.reset).pack(side="right", padx=(10, 0))
        self.demo_button = self._button(header, "Demo traffic", self.demo)
        self.demo_button.pack(side="right")

        stats = tk.Frame(outer, bg=BG)
        stats.pack(fill="x", pady=(0, 12))
        self.stat_labels = {}
        for column, (key, title_text) in enumerate((
                ("available", "AVAILABLE LIFTS"), ("active", "ACTIVE HALL CALLS"), ("completed", "CALLS SERVED"))):
            stats.columnconfigure(column, weight=1)
            card = tk.Frame(stats, bg=PAPER, padx=14, pady=8)
            card.grid(row=0, column=column, sticky="ew", padx=(0, 10) if column < 2 else 0)
            self._label(card, title_text, fg=MUTED, size=8, bold=True).pack(side="left")
            label = self._label(card, "0", size=18, bold=True)
            label.pack(side="right")
            self.stat_labels[key] = label

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=350)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=PAPER, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self.canvas.bind("<Configure>", self._draw_building)
        for floor in range(1, FLOORS + 1):
            for direction, symbol in ((Direction.UP, "▲"), (Direction.DOWN, "▼")):
                if (floor == 1 and direction is Direction.DOWN) or (floor == FLOORS and direction is Direction.UP):
                    continue
                self.hall_buttons[floor, direction] = self._button(
                    self.canvas, symbol, lambda f=floor, d=direction: self.system.hardware.press_hall(f, d),
                    font=font(11, True),
                )

        sidebar = tk.Frame(body, bg=BG, width=350)
        sidebar.grid(row=0, column=1, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(2, weight=1)
        for row, car_id in enumerate(("A", "B")):
            self._build_car_card(sidebar, row, car_id)
        activity = tk.Frame(sidebar, bg=PAPER, padx=14, pady=10)
        activity.grid(row=2, column=0, sticky="nsew")
        self._label(activity, "DISPATCH & SERVICE LOG", fg=MUTED, size=8, bold=True).pack(anchor="w")
        self.activity_text = tk.Text(activity, height=4, width=36, wrap="word", relief="flat", bd=0,
                                     bg=PAPER, fg="#55677e", font=font(9), state="disabled", spacing3=4)
        self.activity_text.pack(fill="both", expand=True, pady=(6, 0))
        self.footer = self._label(outer, "", bg=BG, fg=MUTED, size=9, anchor="w")
        self.footer.pack(fill="x", side="bottom", before=body, pady=(10, 0))

    def _build_car_card(self, sidebar, row, car_id):
        color = CAR_COLORS[car_id]
        card = tk.Frame(sidebar, bg=NAVY, padx=14, pady=8)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        heading = tk.Frame(card, bg=NAVY)
        heading.pack(fill="x")
        self._label(heading, f"LIFT {car_id}  /  CABIN CONTROLS", bg=NAVY, fg="#b4c6d8", size=8, bold=True).pack(side="left")
        badge = self._label(heading, "IDLE", bg=color, fg=PAPER, size=8, bold=True, padx=7, pady=2)
        badge.pack(side="right")
        readout = tk.Frame(card, bg=NAVY)
        readout.pack(fill="x")
        floor = self._label(readout, "01", bg=NAVY, fg=PAPER, size=28, bold=True)
        floor.pack(side="left")
        details = self._label(readout, "AT REST\nDoors closed", bg=NAVY, fg="#b4c6d8", size=9, justify="right")
        details.pack(side="right")
        route = self._label(card, "No stops queued", bg=NAVY, fg="#b4c6d8", size=9, anchor="w", wraplength=310)
        route.pack(fill="x", pady=(0, 3))
        keypad = tk.Frame(card, bg=NAVY)
        keypad.pack(fill="x")
        self.cabin_buttons[car_id] = {}
        for index in range(10):
            keypad.columnconfigure(index % 5, weight=1)
            destination = index + 1
            button = tk.Button(keypad, text=f"{destination:02d}", relief="flat", bd=0, cursor="hand2",
                               bg="#2a4059", fg=PAPER, activebackground=AMBER, activeforeground=INK,
                               font=font(10, True), pady=1, highlightthickness=0,
                               command=lambda c=car_id, f=destination: self.system.hardware.press_cabin(c, f))
            button.grid(row=index // 5, column=index % 5, sticky="ew", padx=2, pady=2)
            self.cabin_buttons[car_id][destination] = button
        fault = tk.Button(card, text=f"Disable lift {car_id} (fault demo)", relief="flat", bd=0,
                          bg=NAVY, fg="#d6a18e", activebackground="#334155", activeforeground=PAPER,
                          cursor="hand2", font=font(8), anchor="w",
                          command=lambda: self.system.hardware.fail_car(car_id))
        fault.pack(fill="x", pady=(2, 0))
        self.car_widgets[car_id] = {"floor": floor, "details": details, "route": route, "badge": badge, "fault": fault}

    def _draw_building(self, _event=None):
        canvas = self.canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 200 or height < 200:
            return
        self._drawing_ready = False
        canvas.delete("all")
        self._row_height = (height - 55) / FLOORS
        self._floor_y = {}
        self._wait_displays = {}
        self._car_items = {}
        self._centers = {"A": width * 0.65, "B": width * 0.87}
        canvas.create_text(39, 23, text="FLOOR", fill=MUTED, font=font(8, True))
        canvas.create_text(120, 23, text="HALL CALL", fill=MUTED, font=font(8, True))
        canvas.create_text(239, 15, text="WAIT TIME / ASSIGNED", fill=MUTED, font=font(8, True))
        canvas.create_text(202, 35, text="▲ UP", fill=MUTED, font=font(7, True))
        canvas.create_text(281, 35, text="▼ DOWN", fill=MUTED, font=font(7, True))
        for car_id, x in self._centers.items():
            canvas.create_text(x, 23, text=f"LIFT {car_id}", fill=CAR_COLORS[car_id], font=font(10, True))
            canvas.create_rectangle(x - 51, 48, x + 51, height - 7, fill=CAR_PALE[car_id], outline="")
        for floor in range(FLOORS, 0, -1):
            y = 48 + (FLOORS - floor + 0.5) * self._row_height
            self._floor_y[floor] = y
            canvas.create_line(15, y + self._row_height / 2, width - 15, y + self._row_height / 2, fill=LINE)
            canvas.create_text(39, y, text=f"{floor:02d}", fill=INK, font=font(18, True))
            for direction, x in ((Direction.UP, 99), (Direction.DOWN, 141)):
                button = self.hall_buttons.get((floor, direction))
                if button:
                    canvas.create_window(x, y, window=button, width=34, height=min(31, self._row_height - 8))
                else:
                    canvas.create_text(x, y, text="—", fill=MUTED, font=font(10))
            for direction, x in ((Direction.UP, 202), (Direction.DOWN, 281)):
                if (floor, direction) in self.hall_buttons:
                    background = canvas.create_rectangle(x - 36, y - 13, x + 36, y + 13, fill=BG, outline="")
                    label = canvas.create_text(x, y, text="—", fill=MUTED, font=font(9, True))
                    self._wait_displays[floor, direction] = background, label
        for car_id, x in self._centers.items():
            for rail_x in (x - 40, x + 40):
                canvas.create_line(rail_x, 50, rail_x, height - 10, fill="#c3cedb")
            self._car_items[car_id] = {
                "target": canvas.create_text(0, 0, text="◀", fill=CAR_COLORS[car_id], font=font(12), state="hidden"),
                "body": canvas.create_rectangle(0, 0, 0, 0, fill=CAR_COLORS[car_id], outline=NAVY),
                "inside": canvas.create_rectangle(0, 0, 0, 0, fill="#b9dfca", outline=""),
                "left": canvas.create_rectangle(0, 0, 0, 0, fill="#c2ced9", outline=""),
                "right": canvas.create_rectangle(0, 0, 0, 0, fill="#a3b4c5", outline=""),
                "label": canvas.create_text(0, 0, text=f"LIFT {car_id}", fill=PAPER, font=font(7, True)),
            }
        self._drawing_ready = True
        self._render()

    def _render(self):
        state = self.system.snapshot
        physical = {car_id: hardware.snapshot for car_id, hardware in self.system.hardware.cars.items()}
        statuses = {car.elevator_id: car.controller for car in state.cars}
        available = sum(not status.fault for status in statuses.values())
        self.stat_labels["available"].configure(text=f"{available} / 2")
        self.stat_labels["active"].configure(text=str(len(state.calls)))
        self.stat_labels["completed"].configure(text=str(state.completed_calls))
        self.demo_button.configure(state="normal" if state.idle else "disabled")
        active = {(call.request.floor, call.request.direction): call for call in state.calls}
        for key, button in self.hall_buttons.items():
            button.configure(bg=AMBER if key in active else BG, state="disabled" if state.error else "normal")
        for car_id, view in physical.items():
            status, widgets = statuses[car_id], self.car_widgets[car_id]
            if status.fault:
                phase = "FAULT"
            elif view.motion is MotionState.MOVING:
                phase = "MOVING UP" if view.direction is Direction.UP else "MOVING DOWN"
            else:
                phase = "IDLE" if status.idle else view.doors.value.upper()
            widgets["badge"].configure(text=phase, bg="#b65341" if status.fault else CAR_COLORS[car_id])
            widgets["floor"].configure(text=f"{view.display_floor:02d}")
            direction = {Direction.UP: "▲ UP", Direction.DOWN: "▼ DOWN", None: "AT REST"}[view.display_direction]
            widgets["details"].configure(text=f"{direction}\nDoors {view.doors.value}")
            route = " → ".join(str(stop.floor) for stop in status.route.stops) if status.route else (
                f"Serving floor {status.service_stop.floor}" if status.service_stop else "No stops queued")
            widgets["route"].configure(text=f"Route: {route}")
            widgets["fault"].configure(state="disabled" if status.fault or state.error else "normal")
            cabin = {request.floor for request in status.cabin_requests}
            for floor, button in self.cabin_buttons[car_id].items():
                button.configure(bg=AMBER if floor in cabin else "#2a4059", fg=INK if floor in cabin else PAPER,
                                 state="disabled" if status.fault or state.error else "normal")
        uncertain = sum(call.state == "uncertain" for call in state.calls)
        notice = "Shared hall buttons • Colored letter = assigned lift • Times show elapsed waiting"
        if state.error:
            notice = state.error
        elif uncertain:
            notice = "Assignment acknowledgment overdue. Ownership retained; reset to restart the demo."
        elif not available:
            notice = "Both lifts unavailable. Hall calls remain waiting. Reset to restore the simulation."
        self.footer.configure(text=notice, fg="#b44331" if state.error or uncertain or not available else MUTED)
        if state.activity and state.activity[-1].sequence != self._last_activity:
            self._last_activity = state.activity[-1].sequence
            lines = []
            for item in state.activity[-22:]:
                minutes, seconds = divmod(max(0, int(item.timestamp - self._started_at)), 60)
                lines.append(f"{minutes:02d}:{seconds:02d}  {item.text}")
            self.activity_text.configure(state="normal")
            self.activity_text.delete("1.0", "end")
            self.activity_text.insert("end", "\n".join(lines))
            self.activity_text.see("end")
            self.activity_text.configure(state="disabled")
        if not self._drawing_ready:
            return
        now = self.system.clock()
        for key, (background, label) in self._wait_displays.items():
            call = active.get(key)
            if call:
                minutes, seconds = divmod(max(0, int(now - call.request.created_at)), 60)
                owner = call.elevator_id or "?"
                suffix = "!" if call.state == "uncertain" else ""
                self.canvas.itemconfigure(label, text=f"{owner}{suffix} {minutes:02d}:{seconds:02d}", fill=INK)
                self.canvas.itemconfigure(background, fill=CAR_PALE.get(owner, "#fff0d1"))
            else:
                self.canvas.itemconfigure(label, text="—", fill=MUTED)
                self.canvas.itemconfigure(background, fill=BG)
        for car_id, view in physical.items():
            self._render_car(car_id, view, bool(statuses[car_id].fault))

    def _render_car(self, car_id, view, faulted):
        canvas, items = self.canvas, self._car_items[car_id]
        x = self._centers[car_id]
        y = self._floor_y[1] - (view.position - 1) * self._row_height
        height = min(44, self._row_height * 0.8)
        top, bottom, left, right = y - height / 2, y + height / 2, x - 36, x + 36
        canvas.coords(items["body"], left, top, right, bottom)
        canvas.itemconfigure(items["body"], fill="#b65341" if faulted else CAR_COLORS[car_id])
        canvas.coords(items["inside"], left + 4, top + 12, right - 4, bottom - 3)
        door_width = (right - left - 8) / 2 * (1 - view.door_open_fraction)
        canvas.coords(items["left"], left + 4, top + 12, left + 4 + door_width, bottom - 3)
        canvas.coords(items["right"], right - 4 - door_width, top + 12, right - 4, bottom - 3)
        canvas.coords(items["label"], x, top + 6)
        if view.target_floor is not None and not faulted:
            canvas.coords(items["target"], x + 61, self._floor_y[view.target_floor])
            canvas.itemconfigure(items["target"], state="normal")
        else:
            canvas.itemconfigure(items["target"], state="hidden")

    def demo(self):
        self.system.hardware.press_cabin("A", 7)
        self.system.hardware.press_cabin("B", 4)
        self.system.hardware.press_hall(3, Direction.UP)
        self.system.hardware.press_hall(8, Direction.DOWN)

    def _tick(self):
        if self._closed:
            return
        self.system.hardware.tick()
        self._render()
        self._timer = self.root.after(25, self._tick)

    def reset(self):
        self.system.stop()
        self._new_system()
        self._last_activity = -1
        self.system.start()
        self._render()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._timer:
            self.root.after_cancel(self._timer)
        try:
            self.system.stop()
        except TimeoutError as error:
            messagebox.showerror("Shutdown error", str(error), parent=self.root)
        finally:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Two elevators with shared hall calls and separate cabin controls.")
    parser.add_argument("--policy", choices=("service-sweep", "baseline"), default="service-sweep")
    args = parser.parse_args()
    root = tk.Tk()
    GroupSimulatorApp(root, policy_factory=BaselinePolicy if args.policy == "baseline" else SchedulingPolicy)
    root.mainloop()
