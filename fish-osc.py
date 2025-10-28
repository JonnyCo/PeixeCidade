import time
import math
import threading
import argparse
import os
import json
from dynamixel_sdk import *  # Uses Dynamixel SDK library
from pythonosc import dispatcher
from pythonosc import osc_server

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

# OSC Configuration
DEFAULT_OSC_IP = "127.0.0.1"
DEFAULT_OSC_PORT = 8000

def degrees_to_dxl_units(degrees):
    return int((degrees / 360.0) * 4095)

def dxl_units_to_degrees(units):
    return (units / 4095.0) * 360

def save_config(listen_ip, listen_port):
    """Save OSC configuration to JSON file"""
    config_file = "settings.json"
    config = {
        "osc": {
            "listen_ip": listen_ip,
            "listen_port": listen_port
        },
        "motor": {
            "port": PORT,
            "baudrate": BAUDRATE,
            "motor_id": MOTOR_ID,
            "home_degrees": HOME_DEGREES
        },
        "default_values": {
            "amplitude_deg": 30.0,
            "speed": 5
        }
    }
    
    try:
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=4)
        print(f"Configuration saved to {config_file}")
    except Exception as e:
        print(f"Error saving config: {e}")

def load_config():
    """Load OSC configuration from JSON file"""
    config_file = "settings.json"
    if not os.path.exists(config_file):
        return None
    
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        return config
    except Exception as e:
        print(f"Error loading config: {e}")
        return None

class SingleMotorOscillator:
    def __init__(self, osc_ip=DEFAULT_OSC_IP, osc_port=DEFAULT_OSC_PORT):
        # Load configuration
        config = load_config()
        
        # Initialize motor connection
        self.port_handler = PortHandler(PORT)
        self.packet_handler = PacketHandler(PROTOCOL_VERSION)

        if not self.port_handler.openPort() or not self.port_handler.setBaudRate(BAUDRATE):
            raise Exception("Failed to open port or set baudrate!")

        # Enable motor and set home position
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_ENABLE)
        self.zero_pos = degrees_to_dxl_units(HOME_DEGREES)
        self.running = False
        
        # Load OSC parameters from config or use defaults
        if config and "default_values" in config:
            self.amplitude_deg = config["default_values"].get("amplitude_deg", 30.0)
            self.speed = config["default_values"].get("speed", 5)
        else:
            self.amplitude_deg = 30.0
            self.speed = 5
        
        # Store OSC config for updates
        self.osc_ip = osc_ip
        self.osc_port = osc_port
        
        # Setup OSC server
        self.dispatcher = dispatcher.Dispatcher()
        self.setup_osc_handlers()
        
        self.osc_server = osc_server.ThreadingOSCUDPServer((osc_ip, osc_port), self.dispatcher)
        print(f"OSC Server listening on {osc_ip}:{osc_port}")
        print("Ready to receive OSC messages:")
        print("  /fish/amplitude <value>")
        print("  /fish/speed <value>")
        print("  /fish/start")
        print("  /fish/stop")
        print("  /fish/status")
        print("  /fish/save")
        print(f"Current settings: amplitude={self.amplitude_deg}°, speed={self.speed}")

    def setup_osc_handlers(self):
        """Setup OSC message handlers"""
        self.dispatcher.map("/fish/amplitude", self.set_amplitude)
        self.dispatcher.map("/fish/speed", self.set_speed)
        self.dispatcher.map("/fish/start", self.start_oscillation)
        self.dispatcher.map("/fish/stop", self.stop_oscillation)
        self.dispatcher.map("/fish/status", self.send_status)
        self.dispatcher.map("/fish/save", self.save_settings)

    def move_to_position(self, position):
        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_GOAL_POSITION, position)

    def set_motor_speed(self, speed):
        self.packet_handler.write2ByteTxRx(self.port_handler, MOTOR_ID, ADDR_MOVING_SPEED, speed)

    def set_amplitude(self, unused_addr, args):
        """OSC handler for setting amplitude"""
        try:
            amplitude = float(args)
            if amplitude <= 0:
                print("Amplitude must be positive")
                return
            self.amplitude_deg = amplitude
            print(f"Amplitude set to {amplitude} degrees")
            self.update_config()
        except (ValueError, TypeError):
            print("Invalid amplitude value")

    def set_speed(self, unused_addr, args):
        """OSC handler for setting speed"""
        try:
            speed = int(args)
            if speed <= 0:
                print("Speed must be positive")
                return
            self.speed = speed
            print(f"Speed set to {speed} steps/sec")
            self.update_config()
        except (ValueError, TypeError):
            print("Invalid speed value")

    def update_config(self):
        """Update JSON config file with current values"""
        config_file = "settings.json"
        try:
            # Load existing config
            config = load_config()
            if not config:
                config = {}
            
            # Update OSC settings
            if "osc" not in config:
                config["osc"] = {}
            config["osc"]["listen_ip"] = self.osc_ip
            config["osc"]["listen_port"] = self.osc_port
            
            # Update default values
            if "default_values" not in config:
                config["default_values"] = {}
            
            config["default_values"]["amplitude_deg"] = self.amplitude_deg
            config["default_values"]["speed"] = self.speed
            
            # Save updated config
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=4)
            print("Configuration updated in settings.json")
        except Exception as e:
            print(f"Error updating config: {e}")

    def save_settings(self, unused_addr=None, args=None):
        """Manually save current settings to JSON file"""
        config_file = "settings.json"
        try:
            # Load existing config or create new
            config = load_config()
            if not config:
                config = {}
            
            # Ensure all sections exist
            if "osc" not in config:
                config["osc"] = {}
            if "motor" not in config:
                config["motor"] = {}
            if "default_values" not in config:
                config["default_values"] = {}
            
            # Update all current settings
            config["osc"]["listen_ip"] = self.osc_ip
            config["osc"]["listen_port"] = self.osc_port
            
            config["motor"]["port"] = PORT
            config["motor"]["baudrate"] = BAUDRATE
            config["motor"]["motor_id"] = MOTOR_ID
            config["motor"]["home_degrees"] = HOME_DEGREES
            
            config["default_values"]["amplitude_deg"] = self.amplitude_deg
            config["default_values"]["speed"] = self.speed
            
            # Save to file
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=4)
            
            print("Settings manually saved to settings.json")
            print(f"Saved: amplitude={self.amplitude_deg}°, speed={self.speed}")
            
        except Exception as e:
            print(f"Error saving settings: {e}")

    def start_oscillation(self, unused_addr=None, args=None):
        """OSC handler for starting oscillation"""
        if self.running:
            print("Oscillation already running")
            return

        amplitude_units = degrees_to_dxl_units(self.amplitude_deg)
        self.set_motor_speed(self.speed)
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
                        time.sleep(1 / (self.speed * 10))

        threading.Thread(target=oscillate, daemon=True).start()
        print("Oscillation started")

    def stop_oscillation(self, unused_addr=None, args=None):
        """OSC handler for stopping oscillation"""
        if not self.running:
            print("Oscillation not running")
            return
            
        self.running = False
        self.move_to_position(self.zero_pos)
        print("Oscillation stopped")

    def send_status(self, unused_addr=None, args=None):
        """Print current status to console"""
        print(f"Status: running={self.running}, amplitude={self.amplitude_deg}, speed={self.speed}")

    def run(self):
        """Start the OSC server"""
        try:
            self.osc_server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources"""
        self.running = False
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)
        self.port_handler.closePort()
        print("Cleanup complete.")

def main():
    # Load saved configuration
    saved_config = load_config()
    
    # Get defaults from config or use hardcoded defaults
    if saved_config and "osc" in saved_config:
        default_ip = saved_config["osc"].get("listen_ip", DEFAULT_OSC_IP)
        default_port = saved_config["osc"].get("listen_port", DEFAULT_OSC_PORT)
    else:
        default_ip = DEFAULT_OSC_IP
        default_port = DEFAULT_OSC_PORT
    
    parser = argparse.ArgumentParser(description="Single Motor Oscillator with OSC Control")
    parser.add_argument("--listen-ip", default=default_ip, 
                       help=f"IP address to listen for OSC messages (default: {default_ip})")
    parser.add_argument("--listen-port", type=int, default=default_port,
                       help=f"Port to listen for OSC messages (default: {default_port})")
    
    args = parser.parse_args()
    
    # Save configuration if different from saved
    if not saved_config or args.listen_ip != saved_config.get("osc", {}).get("listen_ip") or args.listen_port != saved_config.get("osc", {}).get("listen_port"):
        save_config(args.listen_ip, args.listen_port)
    
    try:
        oscillator = SingleMotorOscillator(
            osc_ip=args.listen_ip,
            osc_port=args.listen_port
        )
        oscillator.run()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()