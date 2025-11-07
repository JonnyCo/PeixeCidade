import tkinter as tk
from pythonosc.udp_client import SimpleUDPClient

# ---------------- CONFIG ----------------
FISH_LIST = [
    ("10.1.91.101", 8000),
    ("10.1.91.102", 8000),
    ("10.1.91.103", 8000),
]
# ----------------------------------------

# Create OSC clients for each fish
clients = [SimpleUDPClient(ip, port) for ip, port in FISH_LIST]

# Functions to send OSC messages
def start_fish():
    for c in clients:
        c.send_message("/fish/start", None)
    status_label.config(text="🐟 All fish started!", fg="green")

def stop_fish():
    for c in clients:
        c.send_message("/fish/stop", None)
    status_label.config(text="🛑 All fish stopped!", fg="red")

# GUI setup
root = tk.Tk()
root.title("🐠 Fish Control Panel")
root.geometry("300x200")
root.resizable(False, False)

title_label = tk.Label(root, text="Peixe Cidade Control", font=("Helvetica", 14, "bold"))
title_label.pack(pady=10)

start_button = tk.Button(root, text="START ALL FISH", font=("Helvetica", 12), bg="lightgreen", command=start_fish)
start_button.pack(pady=10, fill="x", padx=40)

stop_button = tk.Button(root, text="STOP ALL FISH", font=("Helvetica", 12), bg="salmon", command=stop_fish)
stop_button.pack(pady=10, fill="x", padx=40)

status_label = tk.Label(root, text="Ready", font=("Helvetica", 10))
status_label.pack(pady=10)

root.mainloop()
