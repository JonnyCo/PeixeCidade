#!/usr/bin/env python3
import time
import math
import tkinter as tk
import threading
from dynamixel_sdk import *  # Uses Dynamixel SDK library
import python

# ==================== USER SETTINGS ====================
PORT                 = "/dev/tty.usbserial-FTA7NN86"
MOTOR_ID             = 1
BAUDRATE             = 57600
PROTOCOL_VERSION     = 2.0

HOME_DEGREES         = 180.0     # mechanical center
AMPLITUDE_DEG        = 50.0      # swing amplitude around HOME (degrees)

# Control the motion in **engineering units**
MIN_SPEED_DPS        = 100.0      # deg/sec at slow end of sweep
MAX_SPEED_DPS        = 100.0      # deg/sec at fast end of sweep
PERIOD_SEC           = 15.0     # seconds for MIN→MAX→MIN speed sweep

LOOP_HZ              = 50.0     # control loop frequency (Hz)

SLEEP_AFTER_PERIOD_SEC = 5.0     # pause after each full sweep
SLEEP_AT_CENTER        = True

DISABLE_TORQUE_DURING_SLEEP = True
# =======================================================

# Dynamixel Control Table (Protocol 2.0)
ADDR_TORQUE_ENABLE = 64
ADDR_GOAL_POSITION = 116
TORQUE_ENABLE      = 1
TORQUE_DISABLE     = 0

def degrees_to_dxl_units(deg: float) -> int:
    deg = (deg % 360.0)
    return int(deg / 360.0 * 4095)

def clamp_0_4095(x: int) -> int:
    return 0 if x < 0 else (4095 if x > 4095 else x)

def speed_deg_per_sec(elapsed: float) -> float:
    if PERIOD_SEC <= 0.0:
        return MIN_SPEED_DPS
    phi = 2.0 * math.pi * (elapsed / PERIOD_SEC)
    return MIN_SPEED_DPS + (MAX_SPEED_DPS - MIN_SPEED_DPS) * 0.5 * (1.0 - math.cos(phi))

def main():
    loop_dt = 1.0 / max(LOOP_HZ, 1.0)
    amp_deg = max(AMPLITUDE_DEG, 0.1)

    ph = PortHandler(PORT)
    pk = PacketHandler(PROTOCOL_VERSION)
    if not ph.openPort():
        raise RuntimeError(f"Failed to open port {PORT}")
    if not ph.setBaudRate(BAUDRATE):
        raise RuntimeError(f"Failed to set baudrate {BAUDRATE}")

    pk.write1ByteTxRx(ph, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_ENABLE)
    center_units = degrees_to_dxl_units(HOME_DEGREES)

    pk.write4ByteTxRx(ph, MOTOR_ID, ADDR_GOAL_POSITION, center_units)
    time.sleep(0.3)

    print("Running continuous sinusoid (Ctrl+C to stop).")

    try:
        while True:
            period_start = time.monotonic()
            phase = 0.0

            while True:
                now = time.monotonic()
                elapsed = now - period_start
                if elapsed >= PERIOD_SEC:
                    break

                v_dps = speed_deg_per_sec(elapsed)
                dphase_dt = v_dps / amp_deg  # rad/s

                phase += dphase_dt * loop_dt

                theta_deg = HOME_DEGREES + AMPLITUDE_DEG * math.sin(phase)
                goal_units = clamp_0_4095(degrees_to_dxl_units(theta_deg))
                pk.write4ByteTxRx(ph, MOTOR_ID, ADDR_GOAL_POSITION, goal_units)

                after = time.monotonic()
                sleep_remain = loop_dt - (after - now)
                if sleep_remain > 0:
                    time.sleep(sleep_remain)

            # ---- END OF SWEEP ----
            if SLEEP_AFTER_PERIOD_SEC > 0.0:
                if SLEEP_AT_CENTER:
                    pk.write4ByteTxRx(ph, MOTOR_ID, ADDR_GOAL_POSITION, center_units)
                    time.sleep(0.3)

                if DISABLE_TORQUE_DURING_SLEEP:
                    pk.write1ByteTxRx(ph, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)

                print(f"Sleeping {SLEEP_AFTER_PERIOD_SEC:.1f} sec…")
                time.sleep(SLEEP_AFTER_PERIOD_SEC)

                if DISABLE_TORQUE_DURING_SLEEP:
                    pk.write1ByteTxRx(ph, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_ENABLE)
                    if SLEEP_AT_CENTER:
                        pk.write4ByteTxRx(ph, MOTOR_ID, ADDR_GOAL_POSITION, center_units)
                        time.sleep(0.2)

    except KeyboardInterrupt:
        print("\nStopping...")

    finally:
        pk.write4ByteTxRx(ph, MOTOR_ID, ADDR_GOAL_POSITION, center_units)
        time.sleep(0.2)
        pk.write1ByteTxRx(ph, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)
        ph.closePort()
        print("Done.")

if __name__ == "__main__":
    main()
