#!/usr/bin/env python3
import tkinter as tk
from pythonosc.udp_client import SimpleUDPClient
import time, json, os, threading

# ------------- NETWORK CONFIG -------------
FISH_LIST = [
    ("10.1.91.101", 8000),  # fish1
    ("10.1.91.102", 8000),  # fish2
    ("10.1.91.103", 8000),  # fish3
]
NUM_FISH = 3
HOME_DEG = 180.0

# ------------- STREAMING CONFIG -------------
STREAM_HZ = 10.0                     # set to the Hz your JSON was authored at
TICK_SEC  = 1.0 / STREAM_HZ

# ------------- DANCE ORDER & SLEEPS -------------
# Each tuple: (filename, sleep_after_sec)
DANCE_SEQUENCE = [
    ("fish_angle_dance.json", 5),
    ("fish_pulsing_dance.json", 25),
    #("fish_sine_dance.json", 10),
    # ("Dance_2.json", 10),
    # ("Dance_3.json", 10),
]
# ------------------------------------------------

# Create OSC clients
clients = [SimpleUDPClient(ip, port) for ip, port in FISH_LIST]

# Tk app
root = tk.Tk()
root.title("🐠 Peixe Cidade Control")
root.geometry("380x280")
root.resizable(False, False)

# ---- UI-safe status setter (thread-safe) ----
def ui_status(msg, color="black"):
    root.after(0, lambda: (status_label.config(text=msg, fg=color)))

# State
basic_running = False          # /fish/start mode on Pis
sender_thread = None
stop_flag = threading.Event()

# dance state protected by GIL (simple types)
dances = []                    # loaded dances: list of dicts {frames: List[List[deg]], "hz": (optional)}
dance_idx = 0                  # which dance file in the sequence
frame_idx = 0                  # which frame within the current dance
sleep_until = 0.0              # absolute monotonic time to end a between-dance sleep
cooling = False                # true only during the cooling/sleep window

# ---------- Helpers ----------
def send_all(path, arg=None):
    for c in clients:
        try:
            c.send_message(path, arg)
        except Exception as e:
            print(f"Send {path} failed: {e}")

def send_angles_frame(frame):
    """
    frame: list-like of length NUM_FISH (deg for fish1, fish2, fish3)
    """
    if not isinstance(frame, (list, tuple)) or len(frame) < NUM_FISH:
        frame = [HOME_DEG] * NUM_FISH
    for i, c in enumerate(clients):
        try:
            c.send_message("/fish/angle", float(frame[i]))
        except Exception as e:
            print(f"Angle send failed (fish{i+1}): {e}")

def load_one_dance(path):
    """
    Returns dict: {"frames": [[deg1,deg2,deg3], ...], "hz": optional}
    Raises Exception on hard failure.
    """
    with open(path, "r") as f:
        data = json.load(f)

    if isinstance(data, dict) and "frames" in data:
        frames = data["frames"]
        _hz = float(data.get("hz", STREAM_HZ))
    else:
        frames = data
        _hz = STREAM_HZ

    clean = []
    for fr in frames:
        if not isinstance(fr, (list, tuple)):
            continue
        row = list(fr[:NUM_FISH]) + [HOME_DEG] * max(0, NUM_FISH - len(fr))
        try:
            row = [float(x) for x in row]
        except Exception:
            row = [HOME_DEG] * NUM_FISH
        clean.append(row)

    if not clean:
        raise ValueError(f"No valid frames in {path}")

    return {"frames": clean, "hz": _hz}

def load_all_dances():
    """Loads all dances listed in DANCE_SEQUENCE into global 'dances'."""
    global dances
    dances = []
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for fname, _sleep in DANCE_SEQUENCE:
        path = os.path.join(base_dir, fname)
        try:
            d = load_one_dance(path)
            dances.append(d)
            print(f"[Loaded] {fname}: {len(d['frames'])} frames (hz={d.get('hz', STREAM_HZ)})")
        except Exception as e:
            print(f"[ERROR] Failed to load {fname}: {e}")
            # placeholder "hold-home" dance
            dances.append({"frames": [[HOME_DEG]*NUM_FISH]*int(STREAM_HZ*2), "hz": STREAM_HZ})

# ---------- Basic Oscillation (Pis handle their own sine) ----------
def start_basic_osc():
    global basic_running
    if sender_thread and sender_thread.is_alive():
        ui_status("Stop Dance first.", "orange")
        return
    if basic_running:
        ui_status("Already oscillating.", "blue")
        return
    send_all("/fish/start", None)   # Pis run their own oscillator
    basic_running = True
    ui_status("🐟 Basic Oscillation started", "green")

# ---------- Dance control (threaded sender) ----------
def start_dance():
    """
    Start streaming angles from JSON files at STREAM_HZ in a dedicated thread,
    with sleeps (cooling) between dances, looping the sequence until STOP.
    """
    global sender_thread, basic_running, dance_idx, frame_idx, sleep_until, cooling

    if sender_thread and sender_thread.is_alive():
        ui_status("Dance already running.", "blue")
        return
    if basic_running:
        # No explicit /fish/stop; our angles will take over.
        basic_running = False

    load_all_dances()

    dance_idx = 0
    frame_idx = 0
    sleep_until = 0.0
    cooling = False
    stop_flag.clear()

    sender_thread = threading.Thread(target=_dance_loop, daemon=True)
    sender_thread.start()
    ui_status("💃 Dance streaming from JSON…", "green")

def _dance_loop():
    """
    Tight-timed streaming loop (separate thread).
    Sends frames at precise STREAM_HZ using time.monotonic().
    Between dances: sends /fish/stop once, then sends NO angles until sleep ends.
    """
    global dance_idx, frame_idx, sleep_until, cooling

    next_t = time.monotonic()
    while not stop_flag.is_set():
        now = time.monotonic()
        # pacing
        if now < next_t:
            time.sleep(max(0.0005, next_t - now))
            continue
        next_t += TICK_SEC

        # Sleep/cooling window?
        if sleep_until > 0.0:
            if now >= sleep_until:
                # sleep complete → advance to next dance
                sleep_until = 0.0
                cooling = False
                frame_idx = 0
                dance_idx = (dance_idx + 1) % len(DANCE_SEQUENCE)
                ui_status(f"🔁 Starting next dance ({dance_idx+1}/{len(DANCE_SEQUENCE)})", "green")
                # fall-through to stream first frame this tick
            else:
                # During cooling: DO NOT SEND ANY ANGLES (keep torque off)
                continue

        if not dances:
            # nothing loaded; idle (no angles to avoid re-enabling torque)
            continue

        current = dances[dance_idx]
        frames = current["frames"]

        # End of this dance → send stop once and start timed cooling
        if frame_idx >= len(frames):
            if not cooling:
                cooling = True
                sleep_sec = float(DANCE_SEQUENCE[dance_idx][1])
                send_all("/fish/stop", None)  # torque off exactly once
                ui_status(f"😴 Cooling motors for {sleep_sec:.0f}s…", "blue")
                sleep_until = time.monotonic() + max(0.0, sleep_sec)
            # no angles during sleep
            continue

        # Stream current frame (this implicitly re-enables torque)
        frame = frames[frame_idx]
        send_angles_frame(frame)
        frame_idx += 1

def stop_all():
    """
    Stops dance streaming and tells Pis to torque off.
    """
    global basic_running
    stop_flag.set()
    if sender_thread and sender_thread.is_alive():
        try:
            sender_thread.join(timeout=1.0)
        except Exception:
            pass
    basic_running = False
    send_all("/fish/stop", None)   # firmware disables torque
    ui_status("🛑 All fish stopped", "red")

def on_close():
    try:
        stop_all()
    finally:
        root.destroy()

# ---------- UI ----------
title_label = tk.Label(root, text="Peixe Cidade Control", font=("Helvetica", 16, "bold"))
title_label.pack(pady=12)

start_dance_btn = tk.Button(
    root, text="Start Dance (JSON)",
    font=("Helvetica", 12), bg="lightblue",
    command=start_dance
)
start_dance_btn.pack(pady=6, fill="x", padx=40)

start_basic_btn = tk.Button(
    root, text="Start Basic Oscillation",
    font=("Helvetica", 12), bg="lightgreen",
    command=start_basic_osc
)
start_basic_btn.pack(pady=6, fill="x", padx=40)

stop_button = tk.Button(
    root, text="STOP ALL FISH",
    font=("Helvetica", 12), bg="salmon",
    command=stop_all
)
stop_button.pack(pady=6, fill="x", padx=40)

status_label = tk.Label(root, text="Ready", font=("Helvetica", 10))
status_label.pack(pady=12)

root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()
