import tkinter as tk
from pythonosc.udp_client import SimpleUDPClient
import time, json, os

# ------------- NETWORK CONFIG -------------
FISH_LIST = [
    ("10.1.91.101", 8000),  # fish1
    ("10.1.91.102", 8000),  # fish2
    ("10.1.91.103", 8000),  # fish3
]
FISH_NAMES = ["fish1", "fish2", "fish3"]
NUM_FISH = 3
HOME_DEG = 180.0

# ------------- STREAMING CONFIG -------------
STREAM_HZ = 5.0                       # play out at 25 Hz
FRAME_MS = int(1000.0 / STREAM_HZ)     # Tk after period

# ------------- DANCE ORDER & SLEEPS -------------
# Each tuple: (filename, sleep_after_sec)
DANCE_SEQUENCE = [
    ("dance_test.json", 10),
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

# State
basic_running = False          # /fish/start mode on Pis
dance_running = False          # JSON streaming active
dance_job_id = None            # Tk after() job id
dances = []                    # loaded dances: list of dicts {frames: List[List[deg]], hz: (optional)}
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
        hz = float(data.get("hz", STREAM_HZ))
    else:
        frames = data
        hz = STREAM_HZ

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

    return {"frames": clean, "hz": hz}

def load_all_dances():
    """
    Loads all dances listed in DANCE_SEQUENCE.
    Stores into global 'dances'.
    """
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
            dances.append({"frames": [[HOME_DEG]*NUM_FISH]*int(STREAM_HZ*2), "hz": STREAM_HZ})

# ---------- Basic Oscillation (Pis handle their own sine) ----------
def start_basic_osc():
    global basic_running
    if dance_running:
        status_label.config(text="Stop Dance first.", fg="orange")
        return
    if basic_running:
        status_label.config(text="Already oscillating.", fg="blue")
        return
    send_all("/fish/start", None)   # Pis run their own oscillator
    basic_running = True
    status_label.config(text="🐟 Basic Oscillation started", fg="green")

# ---------- Dance control ----------
def start_dance():
    """
    Start streaming angles from JSON files at STREAM_HZ, with sleeps between dances,
    looping the DANCE_SEQUENCE until STOP is pressed.
    """
    global dance_running, basic_running, dance_idx, frame_idx, sleep_until, cooling
    if dance_running:
        status_label.config(text="Dance already running.", fg="blue")
        return

    if basic_running:
        # No explicit /fish/stop; our angles will take over.
        basic_running = False

    load_all_dances()

    dance_idx = 0
    frame_idx = 0
    sleep_until = 0.0
    cooling = False
    dance_running = True
    status_label.config(text="💃 Dance streaming from JSON…", fg="green")

    _dance_tick()  # kick off

def _dance_tick():
    """
    Called every FRAME_MS while dance_running.
    Streams frames; when a dance ends, sends /fish/stop ONCE and
    **does not send any angles during sleep**; then advances to the next dance.
    """
    global dance_job_id, dance_idx, frame_idx, sleep_until, cooling

    if not dance_running:
        return

    # Handle sleep/cooling window
    if sleep_until > 0.0:
        now = time.monotonic()
        if now >= sleep_until:
            # Sleep done -> advance to next dance
            sleep_until = 0.0
            cooling = False
            frame_idx = 0
            dance_idx = (dance_idx + 1) % len(DANCE_SEQUENCE)
            status_label.config(text=f"🔁 Starting next dance ({dance_idx+1}/{len(DANCE_SEQUENCE)})", fg="green")
            # fall-through to stream first frame of the next dance this tick
        else:
            # During cooling: DO NOT SEND ANGLES (keep torque off)
            _schedule_next_tick()
            return

    # If nothing loaded, idle HOME (optional; but here we also avoid re-enabling torque)
    if not dances:
        _schedule_next_tick()
        return

    current = dances[dance_idx]
    frames = current["frames"]

    # End of current dance → stop once and start timed cooling
    if frame_idx >= len(frames):
        if not cooling:
            cooling = True
            sleep_sec = float(DANCE_SEQUENCE[dance_idx][1])
            send_all("/fish/stop", None)  # torque off exactly once
            status_label.config(text=f"😴 Cooling motors for {sleep_sec:.0f}s…", fg="blue")
            sleep_until = time.monotonic() + max(0.0, sleep_sec)
        # Do not send angles while cooling
        _schedule_next_tick()
        return

    # Stream current frame
    frame = frames[frame_idx]
    send_angles_frame(frame)

    # Advance
    frame_idx += 1

    # Next tick
    _schedule_next_tick()

def _schedule_next_tick():
    global dance_job_id
    if dance_running:
        dance_job_id = root.after(FRAME_MS, _dance_tick)

def stop_all():
    """
    Stops dance streaming and tells Pis to torque off.
    """
    global dance_running, dance_job_id, basic_running, cooling, sleep_until
    if dance_job_id is not None:
        try:
            root.after_cancel(dance_job_id)
        except Exception:
            pass
        dance_job_id = None

    dance_running = False
    basic_running = False
    cooling = False
    sleep_until = 0.0

    send_all("/fish/stop", None)   # firmware disables torque
    status_label.config(text="🛑 All fish stopped", fg="red")

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
