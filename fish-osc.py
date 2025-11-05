import time
import math
import threading
import argparse
import os
import json
import socket
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from dynamixel_sdk import *  # Dynamixel SDK
from pythonosc import dispatcher as osc_dispatcher
from pythonosc import osc_server

# ==================== USER / HW SETTINGS ====================
PORT = "/dev/ttyUSB0"        # U2D2/USB adapter on Pi
BAUDRATE = 57600
PROTOCOL_VERSION = 2.0
MOTOR_ID = 1

HOME_DEGREES = 180.0

# Defaults for engineering-units motion
DEFAULTS_MOTION = {
    "amplitude_deg": 50.0,              # degrees around HOME
    "min_speed_dps": 100.0,             # deg/s at slowest point in sweep
    "max_speed_dps": 200.0,             # deg/s at fastest point in sweep
    "period_sec": 15.0,                 # seconds for MIN->MAX->MIN sweep
    "loop_hz": 50.0,                    # control loop rate
    "sleep_after_period_sec": 5.0,      # pause after each sweep
    "sleep_at_center": True,            # move to center before sleep
    "disable_torque_during_sleep": True # cut torque during sleep
}

# Dynamixel Control Table (XL430 family)
ADDR_TORQUE_ENABLE        = 64   # 1B
ADDR_GOAL_POSITION        = 116  # 4B
ADDR_PRESENT_POSITION     = 132  # 4B
ADDR_PROFILE_ACCELERATION = 108  # 4B
ADDR_PROFILE_VELOCITY     = 112  # 4B
ADDR_VELOCITY_LIMIT       = 44   # 4B
TORQUE_ENABLE             = 1
TORQUE_DISABLE            = 0

# Safe-ish caps — tune for your linkage
VEL_LIMIT_UNITS  = 300   # ~0.229 rpm/unit -> 300 ~ 68.7 rpm ~ 412 deg/s
PROF_VEL_UNITS   = 300
PROF_ACC_UNITS   = 1000

# OSC defaults
DEFAULT_OSC_IP   = "0.0.0.0"
DEFAULT_OSC_PORT = 8000

SETTINGS_FILE = "settings.json"
# ============================================================


# -------------------------- Helpers --------------------------
def degrees_to_dxl_units(deg: float) -> int:
    d = (deg % 360.0)
    return int(d / 360.0 * 4095.0)

def dxl_units_to_degrees(units: int) -> float:
    return (units / 4095.0) * 360.0

def clamp_0_4095(x: int) -> int:
    return 0 if x < 0 else (4095 if x > 4095 else x)

def speed_deg_per_sec(elapsed: float, period_sec: float, vmin: float, vmax: float) -> float:
    """Cosine sweep between vmin and vmax over period_sec."""
    if period_sec <= 0.0:
        return vmin
    phi = 2.0 * math.pi * (elapsed / period_sec)
    return vmin + (vmax - vmin) * 0.5 * (1.0 - math.cos(phi))

def is_display_connected():
    try:
        if not os.environ.get("DISPLAY"):
            return False
        t = tk.Tk(); t.withdraw(); t.destroy()
        return True
    except Exception:
        return False


# ----------------------- Config helpers ----------------------
def load_config():
    if not os.path.exists(SETTINGS_FILE):
        return None
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None

def save_config(listen_ip, listen_port, motion_dict):
    cfg = {
        "osc": {"listen_ip": listen_ip, "listen_port": listen_port},
        "motor": {
            "port": PORT, "baudrate": BAUDRATE,
            "motor_id": MOTOR_ID, "home_degrees": HOME_DEGREES
        },
        "motion": motion_dict
    }
    with open(SETTINGS_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

def merge_motion_defaults(existing):
    m = dict(DEFAULTS_MOTION)
    if existing:
        for k, v in existing.items():
            if k in m:
                m[k] = v
    return m


# ---------------------- Main controller ----------------------
class SingleMotorOscillator:
    def __init__(self, root, osc_ip, osc_port, auto_start=True):
        self.root = root
        self.has_gui = root is not None
        self.auto_start = auto_start

        cfg = load_config() or {}
        osc_cfg = cfg.get("osc", {})
        self.osc_ip = osc_ip if osc_ip is not None else osc_cfg.get("listen_ip", DEFAULT_OSC_IP)
        self.osc_port = int(osc_port if osc_port is not None else osc_cfg.get("listen_port", DEFAULT_OSC_PORT))

        motion_cfg = merge_motion_defaults(cfg.get("motion"))
        self.amplitude_deg  = float(motion_cfg["amplitude_deg"])
        self.min_speed_dps  = float(motion_cfg["min_speed_dps"])
        self.max_speed_dps  = float(motion_cfg["max_speed_dps"])
        self.period_sec     = float(motion_cfg["period_sec"])
        self.loop_hz        = float(motion_cfg["loop_hz"])
        self.sleep_after_s  = float(motion_cfg["sleep_after_period_sec"])
        self.sleep_at_center = bool(motion_cfg["sleep_at_center"])
        self.disable_torque_during_sleep = bool(motion_cfg["disable_torque_during_sleep"])

        # Persist normalized config
        save_config(self.osc_ip, self.osc_port, {
            "amplitude_deg": self.amplitude_deg,
            "min_speed_dps": self.min_speed_dps,
            "max_speed_dps": self.max_speed_dps,
            "period_sec": self.period_sec,
            "loop_hz": self.loop_hz,
            "sleep_after_period_sec": self.sleep_after_s,
            "sleep_at_center": self.sleep_at_center,
            "disable_torque_during_sleep": self.disable_torque_during_sleep
        })

        # DXL setup
        self.port_handler = PortHandler(PORT)
        self.packet_handler = PacketHandler(PROTOCOL_VERSION)
        if not self.port_handler.openPort():
            raise RuntimeError(f"Failed to open port {PORT}")
        if not self.port_handler.setBaudRate(BAUDRATE):
            raise RuntimeError(f"Failed to set baudrate {BAUDRATE}")

        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_ENABLE)
        self._assert_motion_caps()

        self.zero_pos = degrees_to_dxl_units(HOME_DEGREES)
        self._goto_units(self.zero_pos)
        time.sleep(0.2)

        self.running = False
        self._stop_evt = threading.Event()
        self._thread = None

        # GUI
        if self.has_gui:
            self._setup_gui()

        # OSC
        self.dispatcher = None
        self.osc_server = None
        self._start_osc_server(self.osc_ip, self.osc_port)

        self._log(f"Ready. amp={self.amplitude_deg}°, min={self.min_speed_dps}°/s, "
                  f"max={self.max_speed_dps}°/s, T={self.period_sec}s, loop={self.loop_hz}Hz, "
                  f"sleep={self.sleep_after_s}s, center={self.sleep_at_center}, "
                  f"cut_torque={self.disable_torque_during_sleep}, OSC={self.osc_ip}:{self.osc_port}")

        # AUTOSTART: schedule after UI/OSC are live
        if self.auto_start and not self.running:
            if self.has_gui:
                self.root.after(200, self.start_oscillation)  # let the window paint
            else:
                threading.Timer(0.1, self.start_oscillation).start()

    # ----------------- Low-level actions -----------------
    def _goto_units(self, units: int):
        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_GOAL_POSITION, clamp_0_4095(units))

    def _assert_motion_caps(self):
        try:
            self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_VELOCITY_LIMIT, VEL_LIMIT_UNITS)
            self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_PROFILE_VELOCITY, PROF_VEL_UNITS)
            self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_PROFILE_ACCELERATION, PROF_ACC_UNITS)
        except Exception:
            pass

    # ------------------------ GUI ------------------------
    def _setup_gui(self):
        root = self.root
        root.title("Fish Motor OSC Controller (deg/sec)")
        frame = ttk.Frame(root, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")

        # OSC controls (listen IP/port + apply)
        osc_frame = ttk.LabelFrame(frame, text="OSC Settings", padding=8)
        osc_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0,8))
        ttk.Label(osc_frame, text="Listen IP:").grid(row=0, column=0, sticky="w")
        self.var_osc_ip = tk.StringVar(value=self.osc_ip)
        ttk.Entry(osc_frame, textvariable=self.var_osc_ip, width=14).grid(row=0, column=1, padx=6)
        ttk.Label(osc_frame, text="Listen Port:").grid(row=0, column=2, sticky="w")
        self.var_osc_port = tk.IntVar(value=self.osc_port)
        ttk.Entry(osc_frame, textvariable=self.var_osc_port, width=8).grid(row=0, column=3, padx=6)
        ttk.Button(osc_frame, text="Apply OSC", command=self.apply_osc_settings).grid(row=0, column=4, padx=6)

        # Motion controls
        r = 1
        def row(label, var):
            nonlocal r
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky="w")
            e = ttk.Entry(frame, textvariable=var, width=12)
            e.grid(row=r, column=1, sticky="w"); r += 1
            return e

        self.var_amp  = tk.DoubleVar(value=self.amplitude_deg)
        self.var_min  = tk.DoubleVar(value=self.min_speed_dps)
        self.var_max  = tk.DoubleVar(value=self.max_speed_dps)
        self.var_T    = tk.DoubleVar(value=self.period_sec)
        self.var_loop = tk.DoubleVar(value=self.loop_hz)
        self.var_sleep = tk.DoubleVar(value=self.sleep_after_s)
        self.var_center = tk.BooleanVar(value=self.sleep_at_center)
        self.var_cut    = tk.BooleanVar(value=self.disable_torque_during_sleep)

        row("Amplitude (deg):", self.var_amp)
        row("Min Speed (deg/s):", self.var_min)
        row("Max Speed (deg/s):", self.var_max)
        row("Sweep Period (s):", self.var_T)
        row("Loop Rate (Hz):", self.var_loop)
        row("Sleep After Period (s):", self.var_sleep)

        ttk.Checkbutton(frame, text="Sleep at center", variable=self.var_center).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        ttk.Checkbutton(frame, text="Disable torque during sleep", variable=self.var_cut).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

        btns = ttk.Frame(frame); btns.grid(row=r, column=0, columnspan=2, pady=8); r += 1
        ttk.Button(btns, text="Start", command=self.start_oscillation_gui).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="Stop",  command=self.stop_oscillation_gui).grid(row=0, column=1, padx=4)
        ttk.Button(btns, text="Home",  command=self.go_home_gui).grid(row=0, column=2, padx=4)
        ttk.Button(btns, text="Save",  command=self.save_settings_gui).grid(row=0, column=3, padx=4)

        self.log_text = tk.Text(frame, height=12, width=74)
        self.log_text.grid(row=r, column=0, columnspan=2, sticky="nsew")

        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        if self.has_gui and hasattr(self, "log_text"):
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.root.update_idletasks()

    # ----------------- OSC server lifecycle -----------------
    def _setup_osc_handlers(self):
        d = self.dispatcher
        d.map("/fish/start",  self.start_oscillation)
        d.map("/fish/stop",   self.stop_oscillation)
        d.map("/fish/home",   self.go_home)
        d.map("/fish/angle",  self.set_angle)
        d.map("/fish/status", self.send_status)
        d.map("/fish/shutdown", self.shutdown_system)

        d.map("/fish/amplitude", self.osc_set_amplitude)
        d.map("/fish/min_speed", self.osc_set_min_speed)
        d.map("/fish/max_speed", self.osc_set_max_speed)
        d.map("/fish/period",    self.osc_set_period)
        d.map("/fish/loop_hz",   self.osc_set_loop_hz)
        d.map("/fish/sleep_after", self.osc_set_sleep_after)
        d.map("/fish/sleep_at_center", self.osc_set_sleep_at_center)
        d.map("/fish/disable_torque_during_sleep", self.osc_set_disable_torque)
        d.map("/fish/save", self.osc_save)

    def _start_osc_server(self, ip, port):
        # Shutdown any existing server
        self._stop_osc_server()

        # Fresh dispatcher
        self.dispatcher = osc_dispatcher.Dispatcher()
        self._setup_osc_handlers()

        try:
            self.osc_server = osc_server.ThreadingOSCUDPServer((ip, int(port)), self.dispatcher)
            threading.Thread(target=self.osc_server.serve_forever, daemon=True).start()
            self._log(f"[OSC] Listening on {ip}:{port}")
        except Exception as e:
            self.osc_server = None
            self._log(f"[OSC] Failed to bind {ip}:{port} — {e}")

    def _stop_osc_server(self):
        if self.osc_server:
            try:
                self.osc_server.shutdown()
            except Exception:
                pass
            self.osc_server = None

    # GUI action: apply new OSC IP/Port
    def apply_osc_settings(self):
        try:
            new_ip = self.var_osc_ip.get().strip()
            new_port = int(self.var_osc_port.get())
            if not new_ip:
                raise ValueError("IP empty")
            self.osc_ip, self.osc_port = new_ip, new_port
            # Persist with current motion
            self.save_settings()
            # Restart OSC server
            self._start_osc_server(self.osc_ip, self.osc_port)
            messagebox.showinfo("OSC", f"OSC server now on {self.osc_ip}:{self.osc_port}")
        except Exception as e:
            messagebox.showerror("OSC", f"Failed to apply OSC settings: {e}")

    # ------------------- OSC setters -------------------
    def osc_set_amplitude(self, addr, value):
        try: self.amplitude_deg = max(0.1, float(value)); self._log(f"Amplitude={self.amplitude_deg} deg")
        except: self._log("Invalid amplitude")

    def osc_set_min_speed(self, addr, value):
        try: self.min_speed_dps = max(0.1, float(value)); self._log(f"Min speed={self.min_speed_dps} deg/s")
        except: self._log("Invalid min speed")

    def osc_set_max_speed(self, addr, value):
        try: self.max_speed_dps = max(0.1, float(value)); self._log(f"Max speed={self.max_speed_dps} deg/s")
        except: self._log("Invalid max speed")

    def osc_set_period(self, addr, value):
        try: self.period_sec = max(0.1, float(value)); self._log(f"Period={self.period_sec} s")
        except: self._log("Invalid period")

    def osc_set_loop_hz(self, addr, value):
        try: self.loop_hz = max(1.0, float(value)); self._log(f"Loop rate={self.loop_hz} Hz")
        except: self._log("Invalid loop rate")

    def osc_set_sleep_after(self, addr, value):
        try: self.sleep_after_s = max(0.0, float(value)); self._log(f"Sleep after={self.sleep_after_s} s")
        except: self._log("Invalid sleep_after")

    def osc_set_sleep_at_center(self, addr, value):
        try: self.sleep_at_center = bool(int(float(value))); self._log(f"Sleep at center={self.sleep_at_center}")
        except: self._log("Invalid sleep_at_center (use 0/1)")

    def osc_set_disable_torque(self, addr, value):
        try: self.disable_torque_during_sleep = bool(int(float(value))); self._log(f"Disable torque during sleep={self.disable_torque_during_sleep}")
        except: self._log("Invalid disable_torque (use 0/1)")

    def osc_save(self, *args):
        self.save_settings()
        self._log("settings.json saved (OSC)")

    # ------------------- Motion control -------------------
    def _oscillation_loop(self):
        center_units = degrees_to_dxl_units(HOME_DEGREES)

        while not self._stop_evt.is_set():
            loop_dt = 1.0 / max(self.loop_hz, 1.0)
            phase = 0.0
            period_start = time.monotonic()
            amp_deg = max(self.amplitude_deg, 0.1)

            while not self._stop_evt.is_set():
                now = time.monotonic()
                elapsed = now - period_start
                if elapsed >= self.period_sec:
                    break

                v_dps = speed_deg_per_sec(elapsed, self.period_sec, self.min_speed_dps, self.max_speed_dps)
                dphase_dt = v_dps / amp_deg   # θ=A·sin(phase) → peak dθ/dt=A·dphase/dt

                phase += dphase_dt * loop_dt
                theta_deg = HOME_DEGREES + self.amplitude_deg * math.sin(phase)
                goal_units = clamp_0_4095(degrees_to_dxl_units(theta_deg))
                self._goto_units(goal_units)

                after = time.monotonic()
                remain = loop_dt - (after - now)
                if remain > 0:
                    time.sleep(remain)

            if self._stop_evt.is_set():
                break

            if self.sleep_after_s > 0.0:
                if self.sleep_at_center:
                    self._goto_units(center_units)
                    time.sleep(0.3)

                if self.disable_torque_during_sleep:
                    self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)

                self._log(f"Sleeping {self.sleep_after_s:.2f}s…")
                time.sleep(self.sleep_after_s)

                if self.disable_torque_during_sleep:
                    self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_ENABLE)
                    time.sleep(0.05)
                    self._assert_motion_caps()
                    if self.sleep_at_center:
                        self._goto_units(center_units)
                        time.sleep(0.2)

                # Restart sweep fresh
                phase = 0.0
                period_start = time.monotonic()

        self._goto_units(degrees_to_dxl_units(HOME_DEGREES))

    def start_oscillation(self, *args):
        if self.running:
            self._log("Already running")
            return
        try:
            self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_ENABLE)
        except Exception:
            pass
        self._stop_evt.clear()
        self.running = True
        self._thread = threading.Thread(target=self._oscillation_loop, daemon=True)
        self._thread.start()
        self._log("Oscillation started")

    def stop_oscillation(self, *args):
        if not self.running:
            self._log("Not running")
            return
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.running = False
        self._log("Oscillation stopped")

    # ------------------- Utility handlers -------------------
    def send_status(self, *args):
        msg = (f"running={self.running}, amp={self.amplitude_deg}°, "
               f"min={self.min_speed_dps}°/s, max={self.max_speed_dps}°/s, "
               f"T={self.period_sec}s, loop={self.loop_hz}Hz, sleep={self.sleep_after_s}s, "
               f"OSC={self.osc_ip}:{self.osc_port}")
        self._log(msg)

    def set_angle(self, addr, value):
        try:
            ang = float(value)
            self.stop_oscillation()
            self._goto_units(degrees_to_dxl_units(ang))
            self._log(f"Angle set to {ang}°")
        except Exception:
            self._log("Invalid angle")

    def go_home(self, *args):
        self.stop_oscillation()
        self._goto_units(degrees_to_dxl_units(HOME_DEGREES))
        self._log("Homed")

    def shutdown_system(self, *args):
        self._log("Shutdown requested")
        self.stop_oscillation()
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)
        self.port_handler.closePort()
        self._stop_osc_server()
        os.system("sudo shutdown -h now")

    # ------------------- GUI wrappers -------------------
    def start_oscillation_gui(self):
        try:
            self.amplitude_deg = float(self.var_amp.get())
            self.min_speed_dps = float(self.var_min.get())
            self.max_speed_dps = float(self.var_max.get())
            self.period_sec    = float(self.var_T.get())
            self.loop_hz       = float(self.var_loop.get())
            self.sleep_after_s = float(self.var_sleep.get())
            self.sleep_at_center = bool(self.var_center.get())
            self.disable_torque_during_sleep = bool(self.var_cut.get())
        except Exception:
            messagebox.showerror("Error", "Invalid numeric values")
            return
        self.save_settings()
        self.start_oscillation()

    def stop_oscillation_gui(self):
        self.stop_oscillation()

    def go_home_gui(self):
        self.go_home()

    def save_settings_gui(self):
        self.save_settings()
        messagebox.showinfo("Saved", "settings.json updated")

    # ------------------- Save settings -------------------
    def save_settings(self, *args):
        motion = {
            "amplitude_deg": self.amplitude_deg,
            "min_speed_dps": self.min_speed_dps,
            "max_speed_dps": self.max_speed_dps,
            "period_sec": self.period_sec,
            "loop_hz": self.loop_hz,
            "sleep_after_period_sec": self.sleep_after_s,
            "sleep_at_center": self.sleep_at_center,
            "disable_torque_during_sleep": self.disable_torque_during_sleep
        }
        save_config(self.osc_ip, self.osc_port, motion)
        self._log("settings.json updated")

    # ------------------- Cleanup -------------------
    def cleanup(self):
        self.stop_oscillation()
        self._stop_osc_server()
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)
        self.port_handler.closePort()
        self._log("Cleanup complete.")


# --------------------------- Main ---------------------------
def main():
    saved = load_config() or {}
    osc_saved = saved.get("osc", {})
    default_ip = osc_saved.get("listen_ip", DEFAULT_OSC_IP)
    default_port = int(osc_saved.get("listen_port", DEFAULT_OSC_PORT))

    parser = argparse.ArgumentParser(description="Single Motor Oscillator (deg/sec) with OSC + GUI")
    parser.add_argument("--listen-ip", default=default_ip, help="OSC listen IP")
    parser.add_argument("--listen-port", type=int, default=default_port, help="OSC listen port")
    parser.add_argument("--no-gui", action="store_true", help="Force headless mode")
    parser.add_argument("--force-gui", action="store_true", help="Force GUI even if no display")
    parser.add_argument("--auto-start", action="store_true", help="Start oscillation automatically")
    parser.add_argument("--no-auto-start", action="store_true", help="Do not autostart oscillation")
    args = parser.parse_args()

    auto_start = True
    if args.no_auto_start:
        auto_start = False
    elif args.auto_start:
        auto_start = True

    # Decide GUI
    use_gui = False
    if args.force_gui:
        use_gui = True
    elif args.no_gui:
        use_gui = False
    else:
        use_gui = is_display_connected()

    try:
        if use_gui:
            root = tk.Tk()
            app = SingleMotorOscillator(root, args.listen_ip, args.listen_port, auto_start=auto_start)
            root.protocol("WM_DELETE_WINDOW", lambda: (app.cleanup(), root.destroy()))
            root.mainloop()
        else:
            app = SingleMotorOscillator(None, args.listen_ip, args.listen_port, auto_start=auto_start)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nShutting down…")
            finally:
                app.cleanup()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
