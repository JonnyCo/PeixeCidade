import time
import math
import threading
import argparse
import os
import json
import socket
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from dynamixel_sdk import *  # Uses Dynamixel SDK library
from pythonosc import dispatcher
from pythonosc import osc_server

# ==================== USER / HW SETTINGS ====================
PORT = "/dev/ttyUSB0"     # USB2Dynamixel/U2D2 adapter on Pi
BAUDRATE = 57600
PROTOCOL_VERSION = 2.0
MOTOR_ID = 1

HOME_DEGREES = 180.0
DEFAULT_AMPLITUDE_DEG = 50.0

# Engineering-units motion envelope
DEFAULT_MIN_SPEED_DPS = 100.0    # deg/s at slow end of sweep
DEFAULT_MAX_SPEED_DPS = 200.0    # deg/s at fast end of sweep
DEFAULT_PERIOD_SEC    = 15.0     # seconds for MIN→MAX→MIN sweep
DEFAULT_LOOP_HZ       = 50.0     # control loop rate
DEFAULT_SLEEP_AFTER_PERIOD_SEC = 5.0
DEFAULT_SLEEP_AT_CENTER        = True
DEFAULT_DISABLE_TORQUE_DURING_SLEEP = True

# Dynamixel Control Table (Protocol 2.0; XL430 etc.)
ADDR_TORQUE_ENABLE        = 64   # 1B
ADDR_GOAL_POSITION        = 116  # 4B
ADDR_PRESENT_POSITION     = 132  # 4B
ADDR_PROFILE_ACCELERATION = 108  # 4B
ADDR_PROFILE_VELOCITY     = 112  # 4B
ADDR_VELOCITY_LIMIT       = 44   # 4B

# Safe-ish motion caps (tune to your linkage!)
VEL_LIMIT_UNITS  = 300   # ~0.229 rpm/unit -> 300 ~ 68.7 rpm ~ 412 deg/s
PROF_VEL_UNITS   = 300
PROF_ACC_UNITS   = 1000

# OSC defaults
DEFAULT_OSC_IP   = "0.0.0.0"
DEFAULT_OSC_PORT = 8000
# ============================================================


# -------------------------- Helpers --------------------------
def degrees_to_dxl_units(deg: float) -> int:
    d = (deg % 360.0)
    return int(d / 360.0 * 4095.0)

def dxl_units_to_degrees(units: int) -> float:
    return (units / 4095.0) * 360.0

def clamp_0_4095(x: int) -> int:
    return 0 if x < 0 else (4095 if x > 4095 else x)

def speed_deg_per_sec(elapsed: float, period_sec: float,
                      vmin: float, vmax: float) -> float:
    """Cosine sweep between vmin and vmax over period_sec."""
    if period_sec <= 0.0:
        return vmin
    phi = 2.0 * math.pi * (elapsed / period_sec)
    return vmin + (vmax - vmin) * 0.5 * (1.0 - math.cos(phi))


# ------------------------ Display check ----------------------
def is_display_connected():
    display = os.environ.get('DISPLAY')
    if not display:
        return False
    try:
        # Tk test is the most reliable
        test_root = tk.Tk()
        test_root.withdraw()
        test_root.destroy()
        return True
    except Exception:
        return False


# ----------------------- Config helpers ----------------------
def save_config(listen_ip, listen_port, defaults=None):
    config_file = "settings.json"
    config = {
        "osc": {"listen_ip": listen_ip, "listen_port": listen_port},
        "motor": {
            "port": PORT,
            "baudrate": BAUDRATE,
            "motor_id": MOTOR_ID,
            "home_degrees": HOME_DEGREES
        },
        "motion": defaults or {}
    }
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=4)

def load_config():
    config_file = "settings.json"
    if not os.path.exists(config_file):
        return None
    with open(config_file, 'r') as f:
        return json.load(f)


# ---------------------- Main controller ----------------------
class SingleMotorOscillator:
    def __init__(self, root, osc_ip=DEFAULT_OSC_IP, osc_port=DEFAULT_OSC_PORT):
        self.root = root
        self.has_gui = root is not None
        if self.has_gui:
            self.root.title("Fish Motor OSC Controller (deg/sec)")

        cfg = load_config()

        # State vars (engineering units)
        motion_cfg = (cfg or {}).get("motion", {})
        self.amplitude_deg  = float(motion_cfg.get("amplitude_deg", DEFAULT_AMPLITUDE_DEG))
        self.min_speed_dps  = float(motion_cfg.get("min_speed_dps", DEFAULT_MIN_SPEED_DPS))
        self.max_speed_dps  = float(motion_cfg.get("max_speed_dps", DEFAULT_MAX_SPEED_DPS))
        self.period_sec     = float(motion_cfg.get("period_sec", DEFAULT_PERIOD_SEC))
        self.loop_hz        = float(motion_cfg.get("loop_hz", DEFAULT_LOOP_HZ))
        self.sleep_after_s  = float(motion_cfg.get("sleep_after_period_sec", DEFAULT_SLEEP_AFTER_PERIOD_SEC))
        self.sleep_at_center = bool(motion_cfg.get("sleep_at_center", DEFAULT_SLEEP_AT_CENTER))
        self.disable_torque_during_sleep = bool(motion_cfg.get("disable_torque_during_sleep",
                                                               DEFAULT_DISABLE_TORQUE_DURING_SLEEP))

        self.osc_ip = osc_ip
        self.osc_port = osc_port

        # DXL setup
        self.port_handler = PortHandler(PORT)
        self.packet_handler = PacketHandler(PROTOCOL_VERSION)
        if not self.port_handler.openPort() or not self.port_handler.setBaudRate(BAUDRATE):
            raise RuntimeError("Failed to open port or set baudrate!")
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, 1)

        # Motion caps
        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_VELOCITY_LIMIT, VEL_LIMIT_UNITS)
        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_PROFILE_VELOCITY, PROF_VEL_UNITS)
        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_PROFILE_ACCELERATION, PROF_ACC_UNITS)

        self.zero_pos = degrees_to_dxl_units(HOME_DEGREES)
        self.move_to_position(self.zero_pos)

        # Threading / state
        self.running = False
        self._thread = None
        self._stop_evt = threading.Event()

        # GUI (optional)
        if self.has_gui:
            self.setup_gui()

        # OSC
        self.dispatcher = dispatcher.Dispatcher()
        self.setup_osc_handlers()
        try:
            self.osc_server = osc_server.ThreadingOSCUDPServer((self.osc_ip, self.osc_port), self.dispatcher)
            self._osc_thread = threading.Thread(target=self.osc_server.serve_forever, daemon=True)
            self._osc_thread.start()
            self.log("[OSC] Listening on %s:%d" % (self.osc_ip, self.osc_port))
        except Exception as e:
            self.log(f"[OSC] Failed to bind: {e}")
            self.osc_server = None

        self.log(f"Ready. amp={self.amplitude_deg}°, min={self.min_speed_dps}°/s, "
                 f"max={self.max_speed_dps}°/s, T={self.period_sec}s, loop={self.loop_hz}Hz")

    # --------------- Hardware primitives ----------------
    def move_to_position(self, position_units: int):
        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_GOAL_POSITION, clamp_0_4095(position_units))

    # -------------------- GUI ---------------------------
    def setup_gui(self):
        frame = ttk.Frame(self.root, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")

        r = 0
        ttk.Label(frame, text="Amplitude (deg):").grid(row=r, column=0, sticky="w")
        self.var_amp = tk.DoubleVar(value=self.amplitude_deg)
        ttk.Entry(frame, textvariable=self.var_amp, width=10).grid(row=r, column=1); r += 1

        ttk.Label(frame, text="Min Speed (deg/s):").grid(row=r, column=0, sticky="w")
        self.var_min = tk.DoubleVar(value=self.min_speed_dps)
        ttk.Entry(frame, textvariable=self.var_min, width=10).grid(row=r, column=1); r += 1

        ttk.Label(frame, text="Max Speed (deg/s):").grid(row=r, column=0, sticky="w")
        self.var_max = tk.DoubleVar(value=self.max_speed_dps)
        ttk.Entry(frame, textvariable=self.var_max, width=10).grid(row=r, column=1); r += 1

        ttk.Label(frame, text="Sweep Period (s):").grid(row=r, column=0, sticky="w")
        self.var_T = tk.DoubleVar(value=self.period_sec)
        ttk.Entry(frame, textvariable=self.var_T, width=10).grid(row=r, column=1); r += 1

        ttk.Label(frame, text="Loop Rate (Hz):").grid(row=r, column=0, sticky="w")
        self.var_loop = tk.DoubleVar(value=self.loop_hz)
        ttk.Entry(frame, textvariable=self.var_loop, width=10).grid(row=r, column=1); r += 1

        ttk.Label(frame, text="Sleep After Period (s):").grid(row=r, column=0, sticky="w")
        self.var_sleep = tk.DoubleVar(value=self.sleep_after_s)
        ttk.Entry(frame, textvariable=self.var_sleep, width=10).grid(row=r, column=1); r += 1

        self.var_center = tk.BooleanVar(value=self.sleep_at_center)
        ttk.Checkbutton(frame, text="Sleep at center", variable=self.var_center).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

        self.var_cut = tk.BooleanVar(value=self.disable_torque_during_sleep)
        ttk.Checkbutton(frame, text="Disable torque during sleep", variable=self.var_cut).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

        btns = ttk.Frame(frame); btns.grid(row=r, column=0, columnspan=2, pady=6); r += 1
        ttk.Button(btns, text="Start", command=self.start_oscillation_gui).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="Stop",  command=self.stop_oscillation_gui).grid(row=0, column=1, padx=4)
        ttk.Button(btns, text="Home",  command=self.go_home_gui).grid(row=0, column=2, padx=4)
        ttk.Button(btns, text="Save",  command=self.save_settings_gui).grid(row=0, column=3, padx=4)

        # Log
        self.log_text = tk.Text(frame, height=10, width=64)
        self.log_text.grid(row=r, column=0, columnspan=2, sticky="nsew")

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        if self.has_gui and hasattr(self, "log_text"):
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.root.update_idletasks()

    # --------------------- OSC --------------------------
    def setup_osc_handlers(self):
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

    # ------------- OSC setters (engineering units) -------------
    def osc_set_amplitude(self, addr, value):
        try:
            v = float(value)
            if v <= 0: raise ValueError
            self.amplitude_deg = v
            self.log(f"Amplitude = {v} deg")
        except Exception:
            self.log("Invalid amplitude")

    def osc_set_min_speed(self, addr, value):
        try:
            v = float(value); self.min_speed_dps = max(0.1, v)
            self.log(f"Min speed = {self.min_speed_dps} deg/s")
        except Exception:
            self.log("Invalid min speed")

    def osc_set_max_speed(self, addr, value):
        try:
            v = float(value); self.max_speed_dps = max(0.1, v)
            self.log(f"Max speed = {self.max_speed_dps} deg/s")
        except Exception:
            self.log("Invalid max speed")

    def osc_set_period(self, addr, value):
        try:
            v = float(value); self.period_sec = max(0.1, v)
            self.log(f"Period = {self.period_sec} s")
        except Exception:
            self.log("Invalid period")

    def osc_set_loop_hz(self, addr, value):
        try:
            v = float(value); self.loop_hz = max(1.0, v)
            self.log(f"Loop rate = {self.loop_hz} Hz")
        except Exception:
            self.log("Invalid loop rate")

    def osc_set_sleep_after(self, addr, value):
        try:
            v = float(value); self.sleep_after_s = max(0.0, v)
            self.log(f"Sleep after period = {self.sleep_after_s} s")
        except Exception:
            self.log("Invalid sleep_after")

    def osc_set_sleep_at_center(self, addr, value):
        try:
            # interpret nonzero as True
            self.sleep_at_center = bool(int(float(value)))
            self.log(f"Sleep at center = {self.sleep_at_center}")
        except Exception:
            self.log("Invalid sleep_at_center (use 0/1)")

    def osc_set_disable_torque(self, addr, value):
        try:
            self.disable_torque_during_sleep = bool(int(float(value)))
            self.log(f"Disable torque during sleep = {self.disable_torque_during_sleep}")
        except Exception:
            self.log("Invalid disable_torque_during_sleep (use 0/1)")

    # ------------------- Motion control -------------------
    def _oscillation_loop(self):
        loop_dt = 1.0 / max(self.loop_hz, 1.0)
        amp_deg = max(self.amplitude_deg, 0.1)
        center_units = degrees_to_dxl_units(HOME_DEGREES)

        while not self._stop_evt.is_set():
            period_start = time.monotonic()
            phase = 0.0

            while not self._stop_evt.is_set():
                now = time.monotonic()
                elapsed = now - period_start
                if elapsed >= self.period_sec:
                    break

                v_dps = speed_deg_per_sec(elapsed, self.period_sec,
                                          self.min_speed_dps, self.max_speed_dps)
                dphase_dt = v_dps / amp_deg  # rad/s since θ=A·sin(phase), peak dθ/dt=A·dphase/dt

                phase += dphase_dt * loop_dt

                theta_deg = HOME_DEGREES + self.amplitude_deg * math.sin(phase)
                goal_units = clamp_0_4095(degrees_to_dxl_units(theta_deg))
                self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_GOAL_POSITION, goal_units)

                # hold loop rate
                after = time.monotonic()
                remain = loop_dt - (after - now)
                if remain > 0:
                    time.sleep(remain)

            # end of one full speed-sweep period
            if self._stop_evt.is_set():
                break

            if self.sleep_after_s > 0.0:
                if self.sleep_at_center:
                    self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_GOAL_POSITION, center_units)
                    time.sleep(0.3)
                if self.disable_torque_during_sleep:
                    self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, 0)
                self.log(f"Sleeping {self.sleep_after_s:.2f}s…")
                time.sleep(self.sleep_after_s)
                if self.disable_torque_during_sleep:
                    self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, 1)
                    if self.sleep_at_center:
                        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_GOAL_POSITION, center_units)
                        time.sleep(0.2)

        # on stop: recenter
        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_GOAL_POSITION, degrees_to_dxl_units(HOME_DEGREES))

    def start_oscillation(self, *args):
        if self.running:
            self.log("Already running")
            return
        self._stop_evt.clear()
        self.running = True
        self._thread = threading.Thread(target=self._oscillation_loop, daemon=True)
        self._thread.start()
        self.log("Oscillation started")

    def stop_oscillation(self, *args):
        if not self.running:
            self.log("Not running")
            return
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.running = False
        self.log("Oscillation stopped")

    # ------------------- Utility handlers -------------------
    def send_status(self, *args):
        msg = (f"running={self.running}, amp={self.amplitude_deg}°, "
               f"min={self.min_speed_dps}°/s, max={self.max_speed_dps}°/s, "
               f"T={self.period_sec}s, loop={self.loop_hz}Hz, sleep={self.sleep_after_s}s")
        self.log(msg)

    def set_angle(self, addr, value):
        try:
            ang = float(value)
            self.stop_oscillation()
            self.move_to_position(degrees_to_dxl_units(ang))
            self.log(f"Angle set to {ang}°")
        except Exception:
            self.log("Invalid angle")

    def go_home(self, *args):
        self.stop_oscillation()
        self.move_to_position(degrees_to_dxl_units(HOME_DEGREES))
        self.log("Homed")

    def shutdown_system(self, *args):
        self.log("Shutdown requested")
        self.stop_oscillation()
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, 0)
        self.port_handler.closePort()
        if self.osc_server:
            self.osc_server.shutdown()
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
        self.start_oscillation()

    def stop_oscillation_gui(self):
        self.stop_oscillation()

    def go_home_gui(self):
        self.go_home()

    def save_settings_gui(self):
        self.save_settings()

    # ------------------- Save settings -------------------
    def save_settings(self, *args):
        defaults = {
            "amplitude_deg": self.amplitude_deg,
            "min_speed_dps": self.min_speed_dps,
            "max_speed_dps": self.max_speed_dps,
            "period_sec": self.period_sec,
            "loop_hz": self.loop_hz,
            "sleep_after_period_sec": self.sleep_after_s,
            "sleep_at_center": self.sleep_at_center,
            "disable_torque_during_sleep": self.disable_torque_during_sleep
        }
        save_config(self.osc_ip, self.osc_port, defaults)
        self.log("settings.json updated")

    # ------------------- Cleanup -------------------
    def cleanup(self):
        self.stop_oscillation()
        if self.osc_server:
            self.osc_server.shutdown()
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, 0)
        self.port_handler.closePort()
        self.log("Cleanup complete.")


# --------------------------- Main ---------------------------
def main():
    saved = load_config()
    default_ip = (saved or {}).get("osc", {}).get("listen_ip", DEFAULT_OSC_IP)
    default_port = (saved or {}).get("osc", {}).get("listen_port", DEFAULT_OSC_PORT)

    parser = argparse.ArgumentParser(description="Single Motor Oscillator (deg/sec) with OSC + optional GUI")
    parser.add_argument("--listen-ip", default=default_ip)
    parser.add_argument("--listen-port", type=int, default=default_port)
    parser.add_argument("--no-gui", action="store_true")
    parser.add_argument("--force-gui", action="store_true")
    args = parser.parse_args()

    # Persist OSC endpoints if changed
    if not saved or args.listen_ip != default_ip or args.listen_port != default_port:
        save_config(args.listen_ip, args.listen_port, (saved or {}).get("motion"))

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
            app = SingleMotorOscillator(root, args.listen_ip, args.listen_port)
            root.protocol("WM_DELETE_WINDOW", lambda: (app.cleanup(), root.destroy()))
            root.mainloop()
        else:
            app = SingleMotorOscillator(None, args.listen_ip, args.listen_port)
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
