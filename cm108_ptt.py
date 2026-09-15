"""CM108 PTT Controller.

A big PTT button that drives a GPIO pin on a C-Media CM108/CM119 USB sound card
through its HID interface. Hold the button (or Spacebar) to transmit, or enable
"Toggle mode" to latch transmit on/off with a click. An optional TX timeout
releases PTT automatically after a set number of seconds.

Optionally links to VoiceMeeter: the Mute button of the VoiceMeeter input strip
that uses the CM108 becomes a PTT button (muted = transmitting), kept in sync
with the app in both directions.
"""

import atexit
import ctypes
import json
import math
import os
import struct
import time
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import ttk

import hid

CMEDIA_VID = 0x0D8C
CMEDIA_PIDS = {0x0008, 0x000C, 0x000D, 0x000E, 0x0012, 0x0139, 0x013A, 0x013C}
PTT_GPIO = 3
DEFAULT_TIMEOUT = 120  # seconds, 0 = no timeout
MAX_TIMEOUT = 3600

VM_POLL_MS = 50
VM_RETRY_S = 2.0
VM_ECHO_GUARD_S = 0.4  # ignore stale mute reads right after we set it
VM_MAX_STRIPS = 8

SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "CM108 PTT" / "settings.json"

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
        """Return a list of (path, label, product) for connected C-Media HID interfaces."""
        found = []
        for info in hid.enumerate(CMEDIA_VID):
            if info["product_id"] not in CMEDIA_PIDS:
                continue
            product = (info.get("product_string") or "C-Media USB audio").strip()
            label = f"{product} (PID {info['product_id']:04X})"
            found.append((info["path"], label, product))
        # Give duplicates a number so they can be told apart.
        counts = {}
        numbered = []
        for path, label, product in found:
            counts[label] = counts.get(label, 0) + 1
            if counts[label] > 1:
                label = f"{label} #{counts[label]}"
            numbered.append((path, label, product))
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


class VoiceMeeter:
    """Thin wrapper around the VoiceMeeter Remote API DLL."""

    def __init__(self):
        self.dll = None
        self.logged_in = False

    @staticmethod
    def _install_dir():
        key = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\VB:Voicemeeter {17359A74-1236-5467}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                uninstall = winreg.QueryValueEx(k, "UninstallString")[0]
            return Path(uninstall.strip('"')).parent
        except OSError:
            return Path(r"C:\Program Files (x86)\VB\Voicemeeter")

    def load(self):
        if self.dll is not None:
            return True
        name = "VoicemeeterRemote64.dll" if struct.calcsize("P") == 8 else "VoicemeeterRemote.dll"
        try:
            dll = ctypes.WinDLL(str(self._install_dir() / name))
        except OSError:
            return False
        dll.VBVMR_GetParameterFloat.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_float)]
        dll.VBVMR_SetParameterFloat.argtypes = [ctypes.c_char_p, ctypes.c_float]
        dll.VBVMR_GetParameterStringW.argtypes = [ctypes.c_char_p, ctypes.c_wchar_p]
        dll.VBVMR_GetVoicemeeterType.argtypes = [ctypes.POINTER(ctypes.c_long)]
        self.dll = dll
        return True

    def login(self):
        """Log in once. Returns True if this call just logged in."""
        if self.logged_in:
            return False
        self.logged_in = self.dll.VBVMR_Login() >= 0
        return self.logged_in

    def logout(self):
        if self.logged_in:
            self.dll.VBVMR_Logout()
            self.logged_in = False

    def is_running(self):
        kind = ctypes.c_long()
        return self.dll.VBVMR_GetVoicemeeterType(ctypes.byref(kind)) == 0

    def params_dirty(self):
        """1 = parameters changed, 0 = no change, negative = error."""
        return self.dll.VBVMR_IsParametersDirty()

    def get_float(self, name):
        value = ctypes.c_float()
        if self.dll.VBVMR_GetParameterFloat(name.encode(), ctypes.byref(value)) != 0:
            return None
        return value.value

    def set_float(self, name, value):
        return self.dll.VBVMR_SetParameterFloat(name.encode(), value) == 0

    def get_string(self, name):
        buf = ctypes.create_unicode_buffer(512)
        if self.dll.VBVMR_GetParameterStringW(name.encode(), buf) != 0:
            return None
        return buf.value


def device_matches(vm_device_name, product):
    """True if a VoiceMeeter input device name refers to the CM108 product.

    VoiceMeeter shows Windows names like "Microphone (C-Media USB Headpho",
    often truncated, while the HID product is "C-Media USB Headphone Set".
    """
    name = vm_device_name.strip()
    if "(" in name:
        name = name[name.index("(") + 1:].rstrip(")")
    name, product = name.strip().lower(), product.strip().lower()
    if len(name) < 6 or not product:
        return False
    return product.startswith(name) or name.startswith(product)


class PTTApp:
    def __init__(self, root):
        self.root = root
        self.radio = CM108()
        self.radio_product = None
        self.devices = []
        self.transmitting = False
        self.tx_source = "app"      # what started the current transmission
        self.space_down = False
        self.tx_start = 0.0
        self.tick_id = None
        self.connected_status = ""

        self.vm = VoiceMeeter()
        self.vm_strip = None        # index of the linked strip, None if not linked
        self.vm_mute = None         # last known Mute state of that strip
        self.vm_next_attach = 0.0
        self.vm_guard_until = 0.0

        root.title("CM108 PTT")
        root.geometry("440x540")
        root.minsize(320, 360)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        atexit.register(self.safe_unkey)

        settings = self.load_settings()
        self.toggle_mode = tk.BooleanVar(value=bool(settings.get("toggle_mode", False)))
        self.timeout_var = tk.StringVar(value=str(settings.get("timeout", DEFAULT_TIMEOUT)))
        self.vm_link = tk.BooleanVar(value=bool(settings.get("voicemeeter_link", True)))
        self.device_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.vm_status_var = tk.StringVar()

        self._build_ui()
        self.refresh_devices()
        if not self.vm_link.get():
            self.vm_status_var.set("VoiceMeeter link is off")
        self.vm_poll()

    # ---------------------------------------------------------- settings ----
    @staticmethod
    def load_settings():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        settings = {
            "toggle_mode": self.toggle_mode.get(),
            "timeout": self.get_timeout(),
            "voicemeeter_link": self.vm_link.get(),
        }
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        except OSError:
            pass

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
                                       textvariable=self.timeout_var,
                                       command=self.save_settings)
        self.timeout_box.pack(side="left", padx=(6, 0))
        self.timeout_box.bind("<FocusOut>", lambda _e: self.save_settings())
        self.timeout_box.bind("<Return>", lambda _e: self.root.focus_set())
        # Space in the spinbox should still work the PTT, not type a space.
        self.timeout_box.bind("<KeyPress-space>", self.on_space_press)
        self.timeout_box.bind("<KeyRelease-space>", self.on_space_release)

        vm_row = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        vm_row.pack(fill="x")
        ttk.Checkbutton(vm_row, text="Link to VoiceMeeter (CM108 input Mute = PTT)",
                        variable=self.vm_link, command=self.on_vm_link_changed,
                        takefocus=0).pack(anchor="w")
        ttk.Label(vm_row, textvariable=self.vm_status_var, foreground="#777",
                  padding=(22, 0, 0, 0)).pack(anchor="w")

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
        self.device_combo["values"] = [label for _, label, _ in self.devices]
        if not self.devices:
            self.radio.close()
            self.set_radio_product(None)
            self.device_var.set("")
            self.set_status("No CM108 device found — plug it in and press Refresh")
            self.update_button()
            return
        paths = [path for path, _, _ in self.devices]
        index = paths.index(previous) if previous in paths else 0
        self.device_combo.current(index)
        self.connect(index)

    def on_device_selected(self, _event=None):
        self.root.focus_set()  # keep Spacebar away from the combobox
        self.connect(self.device_combo.current())

    def connect(self, index):
        self.set_tx(False)
        path, label, product = self.devices[index]
        try:
            if self.radio.path != path:
                self.radio.open(path)
            self.radio.set_gpio(PTT_GPIO, False)  # known state: not transmitting
            self.connected_status = f"Connected: {label} — GPIO{PTT_GPIO} = PTT"
            self.set_status(self.connected_status)
            self.set_radio_product(product)
        except OSError as exc:
            self.radio.close()
            self.set_radio_product(None)
            self.set_status(f"Cannot open device: {exc}")
        self.update_button()

    def set_radio_product(self, product):
        if product != self.radio_product:
            self.radio_product = product
            self.vm_detach()  # re-match the VoiceMeeter strip for the new device
            self.vm_next_attach = 0.0

    # ---------------------------------------------------------------- TX ----
    def set_tx(self, state, source="app"):
        if state != self.transmitting:
            if not self.radio.is_open:
                if state:
                    self.set_status("Not connected — press Refresh")
            else:
                try:
                    self.radio.set_gpio(PTT_GPIO, state)
                    self.transmitting = state
                except OSError as exc:
                    self.transmitting = False
                    self.radio.close()
                    self.set_status(f"Device error: {exc} — press Refresh")
                self.update_button()
                if self.transmitting:
                    self.tx_source = source
                    self.tx_start = time.monotonic()
                    self.set_status(self.connected_status)
                    self.tick()
                elif self.tick_id is not None:
                    self.root.after_cancel(self.tick_id)
                    self.tick_id = None
        self.vm_sync()

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

    # -------------------------------------------------------- VoiceMeeter ----
    def vm_poll(self):
        try:
            self._vm_poll()
        except OSError as exc:
            self.vm_detach(f"VoiceMeeter error: {exc}")
        self.root.after(VM_POLL_MS, self.vm_poll)

    def _vm_poll(self):
        if not self.vm_link.get():
            return
        if self.vm_strip is None:
            if time.monotonic() >= self.vm_next_attach:
                self.vm_attach()
            return
        if not self.vm.is_running():
            self.vm_detach("VoiceMeeter is not running")
            return
        if self.vm.params_dirty() != 1:
            return
        strip = f"Strip[{self.vm_strip}]"
        device = self.vm.get_string(f"{strip}.device.name") or ""
        if not device_matches(device, self.radio_product or ""):
            self.vm_detach("CM108 input changed in VoiceMeeter — searching again")
            self.vm_next_attach = 0.0
            return
        mute = self.vm.get_float(f"{strip}.Mute")
        if mute is None:
            return
        mute = mute >= 0.5
        if time.monotonic() < self.vm_guard_until and mute != self.vm_mute:
            return  # VoiceMeeter hasn't applied our last change yet
        if mute != self.vm_mute:
            self.vm_mute = mute
            self.set_tx(mute, source="vm")

    def vm_attach(self):
        """Find the VoiceMeeter input strip that uses the CM108 and link to it."""
        self.vm_next_attach = time.monotonic() + VM_RETRY_S
        if not self.vm.load():
            self.vm_status_var.set("VoiceMeeter is not installed")
            return
        if self.vm.login():
            self.vm_next_attach = time.monotonic() + 0.3  # let parameters load
            self.vm_status_var.set("Connecting to VoiceMeeter…")
            return
        if not self.vm.logged_in or not self.vm.is_running():
            self.vm_status_var.set("VoiceMeeter is not running")
            return
        if not self.radio_product:
            self.vm_status_var.set("No CM108 to match with a VoiceMeeter input")
            return
        self.vm.params_dirty()
        for index in range(VM_MAX_STRIPS):
            device = self.vm.get_string(f"Strip[{index}].device.name")
            if device and device_matches(device, self.radio_product):
                break
        else:
            self.vm_status_var.set(f'No VoiceMeeter input uses "{self.radio_product}"')
            return
        label = self.vm.get_string(f"Strip[{index}].Label") or ""
        mute = self.vm.get_float(f"Strip[{index}].Mute")
        self.vm_strip = index
        self.vm_mute = mute is not None and mute >= 0.5
        name = f"{label} — {device}" if label else device
        self.vm_status_var.set(f"Linked to VoiceMeeter input {index + 1}: {name}")
        self.vm_sync()  # mute must match the TX state (normally: unmuted)

    def vm_detach(self, message=None):
        if self.vm_strip is not None and self.transmitting and self.tx_source == "vm":
            self.vm_strip = None
            self.set_tx(False)
        self.vm_strip = None
        self.vm_mute = None
        self.vm_next_attach = time.monotonic() + VM_RETRY_S
        if message:
            self.vm_status_var.set(message)

    def vm_sync(self):
        """Make the linked strip's Mute match the TX state (muted = TX)."""
        if not self.vm_link.get() or self.vm_strip is None:
            return
        if self.vm_mute == self.transmitting:
            return
        if self.vm.set_float(f"Strip[{self.vm_strip}].Mute", 1.0 if self.transmitting else 0.0):
            self.vm_mute = self.transmitting
            self.vm_guard_until = time.monotonic() + VM_ECHO_GUARD_S

    def on_vm_link_changed(self):
        self.root.focus_set()
        self.set_tx(False)
        self.save_settings()
        if self.vm_link.get():
            self.vm_next_attach = 0.0
            self.vm_status_var.set("Searching for VoiceMeeter…")
        else:
            self.vm_detach("VoiceMeeter link is off")

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
        self.save_settings()

    def on_close(self):
        self.set_tx(False)  # also unmutes the linked VoiceMeeter strip
        self.safe_unkey()
        self.save_settings()
        self.vm.logout()
        self.radio.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    PTTApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
