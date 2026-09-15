"""CM108 PTT Controller.

A big PTT button that drives a GPIO pin on a C-Media CM108/CM119 USB sound card
through its HID interface. Hold the button (or Spacebar) to transmit, or enable
"Toggle mode" to latch transmit on/off with a click. An optional TX timeout
releases PTT automatically after a set number of seconds.
"""

import atexit
import math
import time
import tkinter as tk
from tkinter import ttk

import hid

CMEDIA_VID = 0x0D8C
CMEDIA_PIDS = {0x0008, 0x000C, 0x000D, 0x000E, 0x0012, 0x0139, 0x013A, 0x013C}
PTT_GPIO = 3
DEFAULT_TIMEOUT = 120  # seconds, 0 = no timeout
MAX_TIMEOUT = 3600

IDLE_BG = "#2e7d32"
TX_BG = "#c62828"
WARN_FG = "#ffeb3b"
DISABLED_BG = "#616161"


class CM108:
    """Minimal CM108 GPIO driver using HID output reports."""

    def __init__(self):
        self._dev = None
        self.path = None

    @staticmethod
    def find_devices():
        """Return a list of (path, label) for connected C-Media HID interfaces."""
        found = []
        for info in hid.enumerate(CMEDIA_VID):
            if info["product_id"] not in CMEDIA_PIDS:
                continue
            name = (info.get("product_string") or "C-Media USB audio").strip()
            label = f"{name} (PID {info['product_id']:04X})"
            found.append((info["path"], label))
        # Give duplicates a number so they can be told apart.
        counts = {}
        numbered = []
        for path, label in found:
            counts[label] = counts.get(label, 0) + 1
            numbered.append((path, label if counts[label] == 1 else f"{label} #{counts[label]}"))
        return numbered

    @property
    def is_open(self):
        return self._dev is not None

    def open(self, path):
        self.close()
        dev = hid.device()
        dev.open_path(path)
        self._dev = dev
        self.path = path

    def close(self):
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
        self._dev = None
        self.path = None

    def set_gpio(self, pin, state):
        """Drive GPIO `pin` (1-8) high or low. Raises OSError on failure."""
        if self._dev is None:
            raise OSError("CM108 not connected")
        mask = 1 << (pin - 1)
        # [report id, reserved, output values, direction (1 = output), reserved]
        report = [0x00, 0x00, mask if state else 0x00, mask, 0x00]
        written = self._dev.write(report)
        if written < 0:
            raise OSError(self._dev.error() or "HID write failed")


class PTTApp:
    def __init__(self, root):
        self.root = root
        self.radio = CM108()
        self.devices = []
        self.transmitting = False
        self.space_down = False
        self.tx_start = 0.0
        self.tick_id = None
        self.connected_status = ""

        root.title("CM108 PTT")
        root.geometry("420x480")
        root.minsize(300, 300)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        atexit.register(self.safe_unkey)

        self.toggle_mode = tk.BooleanVar(value=False)
        self.device_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.timeout_var = tk.StringVar(value=str(DEFAULT_TIMEOUT))

        self._build_ui()
        self.refresh_devices()

    # ---------------------------------------------------------------- UI ----
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(10, 10, 10, 0))
        top.pack(fill="x")
        self.device_combo = ttk.Combobox(top, textvariable=self.device_var,
                                         state="readonly", takefocus=0)
        self.device_combo.pack(side="left", fill="x", expand=True)
        self.device_combo.bind("<<ComboboxSelected>>", self.on_device_selected)
        ttk.Button(top, text="Refresh", command=self.refresh_devices,
                   takefocus=0).pack(side="left", padx=(6, 0))

        ttk.Label(self.root, textvariable=self.status_var,
                  padding=(10, 4)).pack(fill="x")

        # Labels behave better than a Button for press/release: Tk keeps the
        # pointer grab on the pressed widget, so release fires even if the
        # mouse drifts off.
        self.ptt = tk.Frame(self.root, bg=DISABLED_BG, relief="raised", bd=4,
                            cursor="hand2")
        self.ptt.pack(fill="both", expand=True, padx=10, pady=6)
        self.ptt_title = tk.Label(self.ptt, text="PTT", fg="white", bg=DISABLED_BG,
                                  font=("Segoe UI", 44, "bold"))
        self.ptt_title.place(relx=0.5, rely=0.45, anchor="center")
        self.ptt_timer = tk.Label(self.ptt, text="", fg="white", bg=DISABLED_BG,
                                  font=("Consolas", 28, "bold"))
        self.ptt_timer.place(relx=0.5, rely=0.72, anchor="center")
        for widget in (self.ptt, self.ptt_title, self.ptt_timer):
            widget.bind("<ButtonPress-1>", self.on_press)
            widget.bind("<ButtonRelease-1>", self.on_release)

        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(fill="x")
        ttk.Checkbutton(bottom, text="Toggle mode (click to latch TX on/off)",
                        variable=self.toggle_mode, command=self.on_mode_changed,
                        takefocus=0).pack(side="left")
        ttk.Label(bottom, text="Space = PTT", foreground="#777").pack(side="right")

        timeout_row = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        timeout_row.pack(fill="x")
        ttk.Label(timeout_row, text="TX timeout (seconds, 0 = off):").pack(side="left")
        self.timeout_box = ttk.Spinbox(timeout_row, from_=0, to=MAX_TIMEOUT,
                                       increment=10, width=6,
                                       textvariable=self.timeout_var)
        self.timeout_box.pack(side="left", padx=(6, 0))
        self.timeout_box.bind("<FocusOut>", lambda _e: self.get_timeout())
        self.timeout_box.bind("<Return>", lambda _e: self.root.focus_set())
        # Space in the spinbox should still work the PTT, not type a space.
        self.timeout_box.bind("<KeyPress-space>", self.on_space_press)
        self.timeout_box.bind("<KeyRelease-space>", self.on_space_release)

        self.root.bind("<KeyPress-space>", self.on_space_press)
        self.root.bind("<KeyRelease-space>", self.on_space_release)
        self.root.bind("<FocusOut>", self.on_focus_out)

    def update_button(self):
        if self.transmitting:
            bg = TX_BG
            self.ptt.config(bg=bg, relief="sunken")
            self.ptt_title.config(text="TRANSMITTING", bg=bg,
                                  font=("Segoe UI", 30, "bold"))
        else:
            bg = IDLE_BG if self.radio.is_open else DISABLED_BG
            self.ptt.config(bg=bg, relief="raised")
            self.ptt_title.config(text="PTT", bg=bg, font=("Segoe UI", 44, "bold"))
            self.ptt_timer.config(text="")
        self.ptt_timer.config(bg=bg)

    def get_timeout(self):
        """Return the TX timeout in seconds (0 = off), fixing invalid input."""
        try:
            value = int(float(self.timeout_var.get()))
        except ValueError:
            value = DEFAULT_TIMEOUT
        value = max(0, min(MAX_TIMEOUT, value))
        if self.timeout_var.get() != str(value):
            self.timeout_var.set(str(value))
        return value

    @staticmethod
    def format_seconds(seconds):
        seconds = max(0, int(seconds))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def set_status(self, text):
        self.status_var.set(text)

    # ----------------------------------------------------------- devices ----
    def refresh_devices(self):
        self.set_tx(False)
        previous = self.radio.path
        self.devices = CM108.find_devices()
        self.device_combo["values"] = [label for _, label in self.devices]
        if not self.devices:
            self.radio.close()
            self.device_var.set("")
            self.set_status("No CM108 device found — plug it in and press Refresh")
            self.update_button()
            return
        paths = [path for path, _ in self.devices]
        index = paths.index(previous) if previous in paths else 0
        self.device_combo.current(index)
        self.connect(index)

    def on_device_selected(self, _event=None):
        self.root.focus_set()  # keep Spacebar away from the combobox
        self.connect(self.device_combo.current())

    def connect(self, index):
        self.set_tx(False)
        path, label = self.devices[index]
        try:
            if self.radio.path != path:
                self.radio.open(path)
            self.radio.set_gpio(PTT_GPIO, False)  # known state: not transmitting
            self.connected_status = f"Connected: {label} — GPIO{PTT_GPIO} = PTT"
            self.set_status(self.connected_status)
        except OSError as exc:
            self.radio.close()
            self.set_status(f"Cannot open device: {exc}")
        self.update_button()

    # ---------------------------------------------------------------- TX ----
    def set_tx(self, state):
        if state == self.transmitting:
            return
        if not self.radio.is_open:
            if state:
                self.set_status("Not connected — press Refresh")
            return
        try:
            self.radio.set_gpio(PTT_GPIO, state)
            self.transmitting = state
        except OSError as exc:
            self.transmitting = False
            self.radio.close()
            self.set_status(f"Device error: {exc} — press Refresh")
        self.update_button()
        if self.transmitting:
            self.tx_start = time.monotonic()
            self.set_status(self.connected_status)
            self.tick()
        elif self.tick_id is not None:
            self.root.after_cancel(self.tick_id)
            self.tick_id = None

    def tick(self):
        """Refresh the on-air timer and enforce the TX timeout."""
        self.tick_id = None
        if not self.transmitting:
            return
        elapsed = time.monotonic() - self.tx_start
        timeout = self.get_timeout()
        if timeout:
            remaining = timeout - elapsed
            if remaining <= 0:
                self.set_tx(False)
                self.set_status(f"TX timeout ({timeout} s) — PTT released")
                return
            # Round up so the display reaches 0:00 exactly at release.
            text = self.format_seconds(math.ceil(remaining))
            fg = WARN_FG if remaining <= 10 else "white"
        else:
            text = self.format_seconds(elapsed)
            fg = "white"
        self.ptt_timer.config(text=text, fg=fg)
        self.tick_id = self.root.after(100, self.tick)

    def safe_unkey(self):
        try:
            if self.radio.is_open:
                self.radio.set_gpio(PTT_GPIO, False)
        except Exception:
            pass
        self.transmitting = False

    # ------------------------------------------------------------ events ----
    def on_press(self, _event=None):
        if self.root.focus_get() is self.timeout_box:
            self.root.focus_set()  # commit the typed timeout value
        if self.toggle_mode.get():
            self.set_tx(not self.transmitting)
        else:
            self.set_tx(True)

    def on_release(self, _event=None):
        if not self.toggle_mode.get():
            self.set_tx(False)

    def on_space_press(self, _event):
        if self.space_down:  # ignore keyboard auto-repeat
            return "break"
        self.space_down = True
        self.on_press()
        return "break"

    def on_space_release(self, _event):
        self.space_down = False
        self.on_release()
        return "break"

    def on_focus_out(self, event):
        # If the window loses focus while Space is held, the release is never
        # delivered — unkey so the radio can't get stuck transmitting.
        if event.widget is self.root and self.space_down:
            self.space_down = False
            if not self.toggle_mode.get():
                self.set_tx(False)

    def on_mode_changed(self):
        self.root.focus_set()
        self.set_tx(False)

    def on_close(self):
        self.safe_unkey()
        self.radio.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    PTTApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
