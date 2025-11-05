import time
import math
from dynamixel_sdk import *  # Dynamixel SDK

# -------------------- USER SETTINGS --------------------
PORT             = "/dev/tty.usbserial-FTA7NN86"
MOTOR_ID         = 1
BAUDRATE         = 57600
PROTOCOL_VERSION = 2.0
HOME_DEGREES     = 180

# The four parameters
AMPLITUDE_DEG = 50.0           # degrees (try 15–30 if motion is hard to see)
SPEED1_SPS    = 1.0           # steps per second (min)
SPEED2_SPS    = 2.0          # steps per second (max)
PERIOD_SEC    = 120.0           # seconds (Speed1 -> Speed2 -> Speed1)

# Match your original stepping behavior
STEPS_PER_HALF = 20           # same as your old code
# -------------------------------------------------------

# Control Table (Protocol 2.0)
ADDR_TORQUE_ENABLE = 64
ADDR_GOAL_POSITION = 116
TORQUE_ENABLE      = 1
TORQUE_DISABLE     = 0

def degrees_to_dxl_units(deg):
    return int((deg / 360.0) * 4095)

def sps_from_time(t):
    """Cosine sweep: s(t) = s1 + (s2-s1)*0.5*(1 - cos(2π t / T))"""
    phi = 2.0 * math.pi * (t / PERIOD_SEC)
    return SPEED1_SPS + (SPEED2_SPS - SPEED1_SPS) * 0.5 * (1.0 - math.cos(phi))

def main():
    ph = PortHandler(PORT)
    pk = PacketHandler(PROTOCOL_VERSION)

    if not ph.openPort() or not ph.setBaudRate(BAUDRATE):
        raise RuntimeError("Failed to open port or set baudrate")

    # Enable torque
    pk.write1ByteTxRx(ph, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_ENABLE)

    center = degrees_to_dxl_units(HOME_DEGREES)
    amp    = degrees_to_dxl_units(AMPLITUDE_DEG)

    # Go to center first
    pk.write4ByteTxRx(ph, MOTOR_ID, ADDR_GOAL_POSITION, center)
    time.sleep(0.2)

    print("Starting oscillation (Ctrl+C to stop).")

    t0 = time.monotonic()
    try:
        while True:
            for direction in (1, -1):
                for i in range(STEPS_PER_HALF + 1):
                    # position target uses the same half-sine you used before
                    s = math.sin((i / STEPS_PER_HALF) * math.pi)  # 0..1..0
                    goal = int(center + direction * amp * s)
                    pk.write4ByteTxRx(ph, MOTOR_ID, ADDR_GOAL_POSITION, goal)

                    # compute current desired steps/sec from the sweep
                    now = time.monotonic()
                    sps = sps_from_time(now - t0)

                    # pace like your original code (sleep scales with "speed")
                    # (you had 1/(speed*10); keep that feel for continuity)
                    sleep_dt = 1.0 / max(sps * 10.0, 0.1)  # avoid zero/very tiny sleeps
                    time.sleep(sleep_dt)

    except KeyboardInterrupt:
        print("\nStopping...")

    finally:
        try:
            pk.write4ByteTxRx(ph, MOTOR_ID, ADDR_GOAL_POSITION, center)
            time.sleep(0.2)
            pk.write1ByteTxRx(ph, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)
        finally:
            ph.closePort()
        print("Done.")

if __name__ == "__main__":
    main()
