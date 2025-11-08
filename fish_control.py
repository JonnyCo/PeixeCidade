import tkinter as tk
from pythonosc.udp_client import SimpleUDPClient
import time, json, os, math

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
STREAM_HZ = 50.0                       # play out at 50Hz
FRAME_MS = int(1000.0 / STREAM_HZ)     # Tk after period

# ------------- DANCE ORDER & SLEEPS -------------
# Put your files in the same folder as this script.
# Each tuple: (filename, sleep_after_sec)
DANCE_SEQUENCE = [
    ("dance_test.json", 5),   # sleep 10s after Dance_1
    #("Dance_2.json", 10),   # sleep 10s after Dance_2
    #("Dance_3.json", 10),   # sleep 10s after Dance_3
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
sleep_job_id = None            # sleep streamer job
dances = []                    # loaded dances: list of dicts {frames: List[List[deg]], hz: (optional)}
dance_idx = 0                  # which dance file in the sequence
frame_idx = 0                  # which frame within the current dance
sleep_until = 0.0              # absolute monotonic time to end a between-dance sleep

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
        # Fallback: if malformed frame, hold home
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

    # light validation / normalization
    clean = []
    for idx, fr in enumerate(frames):
        if not isinstance(fr, (list, tuple)):
            # skip malformed frames
            continue
        # pad / slice to NUM_FISH
        row = list(fr[:NUM_FISH]) + [HOME_DEG] * max(0, NUM_FISH - len(fr))
        # coerce to float
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
            # Insert a placeholder "hold-home" dance to keep the timeline intact
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
    Start streaming angles from JSON files at 50Hz, with sleeps between dances,
    looping the DANCE_SEQUENCE until STOP is pressed.
    """
    global dance_running, basic_running, dance_idx, frame_idx, sleep_until
    if dance_running:
        status_label.config(text="Dance already running.", fg="blue")
        return

    if basic_running:
        # We intentionally do NOT send /fish/stop here; angles will take over.
        basic_running = False

    # Load dances (fresh each time so you can swap files without restarting)
    load_all_dances()

    dance_idx = 0
    frame_idx = 0
    sleep_until = 0.0
    dance_running = True
    status_label.config(text="💃 Dance streaming from JSON…", fg="green")

    _dance_tick()  # kick off

def _dance_tick():
    """
    Called every FRAME_MS while dance_running.
    Streams a frame; when a dance ends, sleeps at HOME for the configured duration,
    then advances to the next dance.
    """
    global dance_job_id, sleep_job_id, dance_idx, frame_idx, sleep_until

    if not dance_running:
        return

    # Are we in a sleep gap between dances?
    if sleep_until > 0.0:
        now = time.monotonic()
        if now >= sleep_until:
            # Sleep done -> advance to next dance
            sleep_until = 0.0
            frame_idx = 0
            dance_idx = (dance_idx + 1) % len(DANCE_SEQUENCE)
            # fall-through to play next frame this tick
        else:
            # Keep streaming HOME during sleep
            send_angles_frame([HOME_DEG]*NUM_FISH)
            _schedule_next_tick()
            return

    # Get current dance and frame
    if not dances:
        # Nothing loaded: hold HOME
        send_angles_frame([HOME_DEG]*NUM_FISH)
        _schedule_next_tick()
        return

    current = dances[dance_idx]
    frames = current["frames"]

    # Safety clamp
    if frame_idx >= len(frames):
        # End of this dance -> start sleep
        sleep_sec = float(DANCE_SEQUENCE[dance_idx][1])
        sleep_until = time.monotonic() + max(0.0, sleep_sec)
        # Immediately stream HOME on this tick
        send_angles_frame([HOME_DEG]*NUM_FISH)
        _schedule_next_tick()
        return

    # Stream the current frame
    frame = frames[frame_idx]
    send_angles_frame(frame)

    # Advance frame for next tick
    frame_idx += 1

    # Schedule the next tick
    _schedule_next_tick()

def _schedule_next_tick():
    global dance_job_id
    if dance_running:
        dance_job_id = root.after(FRAME_MS, _dance_tick)

def stop_all():
    """
    Stops dance streaming (and any pending sleep) and tells Pis to torque off.
    """
    global dance_running, dance_job_id, sleep_job_id, basic_running
    if dance_job_id is not None:
        try:
            root.after_cancel(dance_job_id)
        except Exception:
            pass
        dance_job_id = None

    # No dedicated sleep job (we stream HOME inside _dance_tick), but reset anyway
    dance_running = False
    basic_running = False

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
