import time
import math
import tkinter as tk
import threading
from dynamixel_sdk import *  # Uses Dynamixel SDK library
import python

# Constants
PORT = "/dev/ttyUSB0"  # Update if needed
BAUDRATE = 57600
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132
ADDR_MOVING_SPEED = 112
ADDR_TORQUE_ENABLE = 64
TORQUE_ENABLE = 1
TORQUE_DISABLE = 0
PROTOCOL_VERSION = 2.0
MOTOR_ID = 1
HOME_DEGREES = 180

def degrees_to_dxl_units(degrees):
    return int((degrees / 360.0) * 4095)

def dxl_units_to_degrees(units):
    return (units / 4095.0) * 360

class SingleMotorOscillator:
    def __init__(self, root):
        self.root = root
        self.root.title("Single Motor Oscillator")

        self.port_handler = PortHandler(PORT)
        self.packet_handler = PacketHandler(PROTOCOL_VERSION)

        if not self.port_handler.openPort() or not self.port_handler.setBaudRate(BAUDRATE):
            raise Exception("Failed to open port or set baudrate!")

        # Enable motor and set home position
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_ENABLE)
        self.zero_pos = degrees_to_dxl_units(HOME_DEGREES)
        self.running = False

        # UI
        tk.Label(root, text="Amplitude (degrees):").pack()
        self.amplitude_entry = tk.Entry(root)
        self.amplitude_entry.insert(0, "30")
        self.amplitude_entry.pack()

        tk.Label(root, text="Speed (steps/sec):").pack()
        self.speed_entry = tk.Entry(root)
        self.speed_entry.insert(0, "5")
        self.speed_entry.pack()

        self.start_button = tk.Button(root, text="Start Oscillation", command=self.start_oscillation)
        self.start_button.pack(pady=5)

        self.stop_button = tk.Button(root, text="Stop Oscillation", command=self.stop_oscillation)
        self.stop_button.pack(pady=5)

        self.log = tk.Text(root, height=8, width=40)
        self.log.pack(pady=10)

    def move_to_position(self, position):
        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_GOAL_POSITION, position)

    def set_speed(self, speed):
        self.packet_handler.write2ByteTxRx(self.port_handler, MOTOR_ID, ADDR_MOVING_SPEED, speed)

    def start_oscillation(self):
        try:
            amplitude_deg = float(self.amplitude_entry.get())
            speed = int(self.speed_entry.get())
        except ValueError:
            self.log.insert(tk.END, "Invalid amplitude or speed.\n")
            return

        if amplitude_deg <= 0 or speed <= 0:
            self.log.insert(tk.END, "Amplitude and speed must be positive.\n")
            return

        amplitude_units = degrees_to_dxl_units(amplitude_deg)
        self.set_speed(speed)
        self.running = True

        def oscillate():
            steps = 20
            while self.running:
                for direction in [1, -1]:
                    for i in range(steps + 1):
                        if not self.running:
                            return
                        sine_value = math.sin((i / steps) * math.pi)
                        position = int(self.zero_pos + direction * amplitude_units * sine_value)
                        self.move_to_position(position)
                        time.sleep(1 / (speed * 10))

        threading.Thread(target=oscillate, daemon=True).start()
        self.log.insert(tk.END, "Oscillation started.\n")

    def stop_oscillation(self):
        self.running = False
        self.move_to_position(self.zero_pos)
        self.log.insert(tk.END, "Oscillation stopped.\n")

    def cleanup(self):
        self.running = False
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)
        self.port_handler.closePort()
        print("Cleanup complete.")

# Run the application
if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = SingleMotorOscillator(root)
        root.mainloop()
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'app' in locals():
            app.cleanup()
