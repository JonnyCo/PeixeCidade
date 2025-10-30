import time
import math
import threading
import argparse
import os
import json
import socket
import subprocess
import sys
import signal
import tkinter as tk
from tkinter import ttk, messagebox
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

def is_display_connected():
    """Check if a display is connected and available at runtime"""
    # Method 1: Check DISPLAY environment variable
    display = os.environ.get('DISPLAY')
    if not display:
        print("No DISPLAY environment variable found")
        return False
    
    print(f"DISPLAY environment variable: {display}")
    
    # Method 2: Try to connect to X11 display using xset
    try:
        result = subprocess.run(['xset', 'q'], capture_output=True, timeout=5)
        if result.returncode == 0:
            print("X11 display is accessible via xset")
            return True
        else:
            print("X11 display not accessible via xset")
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        print("xset command failed or not available")
    
    # Method 3: Try to initialize tkinter (most reliable test)
    try:
        test_root = tk.Tk()
        test_root.withdraw()  # Hide the window immediately
        test_root.destroy()
        print("Tkinter display test successful")
        return True
    except tk.TclError as e:
        print(f"Tkinter display test failed: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error during tkinter test: {e}")
        return False

def degrees_to_dxl_units(degrees):
    return int((degrees / 360.0) * 4095)

def dxl_units_to_degrees(units):
    return (units / 4095.0) * 360

def get_local_ips():
    """Get all local IP addresses"""
    ips = []
    
    # Get hostname IPs
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        ips.append(f"{hostname}: {local_ip}")
    except:
        pass
    
    # Get all network interfaces
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
        if result.returncode == 0:
            interface_ips = result.stdout.strip().split()
            for i, ip in enumerate(interface_ips):
                ips.append(f"Interface {i+1}: {ip}")
    except:
        pass
    
    # Fallback to common method
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        ips.append(f"Primary: {local_ip}")
    except:
        ips.append("Unable to detect IP")
    
    return ips

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
    def __init__(self, root, osc_ip=DEFAULT_OSC_IP, osc_port=DEFAULT_OSC_PORT):
        self.root = root
        self.has_gui = root is not None
        
        if self.has_gui:
            self.root.title("Fish Motor OSC Controller")
        
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
        
        # Store auto-start preference
        self.no_auto_start = False
        
        # Initialize OSC availability flag
        self.osc_available = False
        self.osc_server = None
        
        # Setup GUI only if available
        if self.has_gui:
            self.setup_gui()

        self.log_message(f"Current settings: amplitude={self.amplitude_deg}°, speed={self.speed}")
        
        # Auto-start: move to home position and start oscillation FIRST
        self.log_message("Moving to home position...")
        home_angle_units = degrees_to_dxl_units(180)  # Home position at 180 degrees
        self.move_to_position(home_angle_units)
        time.sleep(1)  # 1 second

        # Start oscillation BEFORE attempting OSC setup
        self.log_message("Starting oscillation...")
        self.start_oscillation()
        
        # Now try to setup OSC server (after motor is already oscillating)
        try:
            self.dispatcher = dispatcher.Dispatcher()
            self.setup_osc_handlers()
            
            self.osc_server = osc_server.ThreadingOSCUDPServer((osc_ip, osc_port), self.dispatcher)
            
            # Start OSC server in separate thread
            self.osc_thread = threading.Thread(target=self.osc_server.serve_forever, daemon=True)
            self.osc_thread.start()
            
            self.osc_available = True
            self.log_message(f"OSC Server listening on {osc_ip}:{osc_port}")
            self.log_message("Ready to receive OSC messages:")
            self.log_message("  /fish/amplitude <value>")
            self.log_message("  /fish/speed <value>")
            self.log_message("  /fish/start")
            self.log_message("  /fish/stop")
            self.log_message("  /fish/status")
            self.log_message("  /fish/angle <value>")
            self.log_message("  /fish/home")
            self.log_message("  /fish/shutdown")
            
        except (OSError, socket.gaierror, socket.error, Exception) as e:
            self.log_message(f"Warning: Could not start OSC server on {osc_ip}:{osc_port}")
            self.log_message(f"OSC Error: {e}")
            self.log_message("Continuing with motor oscillation only...")
            self.log_message("Motor control will work, but remote control via OSC is unavailable")

    def setup_gui(self):
        """Setup the tkinter GUI"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        
        # OSC Configuration Frame
        osc_frame = ttk.LabelFrame(main_frame, text="OSC Configuration", padding="5")
        osc_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=5)
        
        ttk.Label(osc_frame, text="Listen IP:").grid(row=0, column=0, sticky=tk.W)
        self.osc_ip_var = tk.StringVar(value=self.osc_ip)
        self.osc_ip_entry = ttk.Entry(osc_frame, textvariable=self.osc_ip_var, width=15)
        self.osc_ip_entry.grid(row=0, column=1, padx=5)
        
        ttk.Label(osc_frame, text="Listen Port:").grid(row=0, column=2, sticky=tk.W, padx=(10,0))
        self.osc_port_var = tk.IntVar(value=self.osc_port)
        self.osc_port_entry = ttk.Entry(osc_frame, textvariable=self.osc_port_var, width=8)
        self.osc_port_entry.grid(row=0, column=3, padx=5)
        
        # IP Addresses Display
        ip_frame = ttk.LabelFrame(main_frame, text="Local IP Addresses", padding="5")
        ip_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=5)
        
        # Get and display IPs
        local_ips = get_local_ips()
        for i, ip_info in enumerate(local_ips):
            ttk.Label(ip_frame, text=ip_info, font=("Courier", 9)).grid(row=i, column=0, sticky=tk.W, pady=1)
        
        # Motor Control Frame
        motor_frame = ttk.LabelFrame(main_frame, text="Motor Control", padding="5")
        motor_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=5)
        
        ttk.Label(motor_frame, text="Amplitude (degrees):").grid(row=0, column=0, sticky=tk.W)
        self.amplitude_var = tk.DoubleVar(value=self.amplitude_deg)
        self.amplitude_entry = ttk.Entry(motor_frame, textvariable=self.amplitude_var, width=10)
        self.amplitude_entry.grid(row=0, column=1, padx=5)
        
        ttk.Label(motor_frame, text="Speed (steps/sec):").grid(row=1, column=0, sticky=tk.W)
        self.speed_var = tk.IntVar(value=self.speed)
        self.speed_entry = ttk.Entry(motor_frame, textvariable=self.speed_var, width=10)
        self.speed_entry.grid(row=1, column=1, padx=5)
        
        # Buttons Frame
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=4, column=0, columnspan=2, pady=10)
        
        self.start_button = ttk.Button(button_frame, text="Start Oscillation", command=self.start_oscillation_gui)
        self.start_button.grid(row=0, column=0, padx=5)
        
        self.stop_button = ttk.Button(button_frame, text="Stop Oscillation", command=self.stop_oscillation_gui)
        self.stop_button.grid(row=0, column=1, padx=5)
        
        self.save_button = ttk.Button(button_frame, text="Save Settings", command=self.save_settings_gui)
        self.save_button.grid(row=0, column=2, padx=5)
        
        self.status_button = ttk.Button(button_frame, text="Get Status", command=self.send_status_gui)
        self.status_button.grid(row=0, column=3, padx=5)
        
        # Log Frame
        log_frame = ttk.LabelFrame(main_frame, text="Log", padding="5")
        log_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=5)
        
        # Create scrolled text widget
        self.log_text = tk.Text(log_frame, height=10, width=60, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(5, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

    def log_message(self, message):
        """Add message to log widget or console"""
        timestamp = time.strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        
        if self.has_gui and hasattr(self, 'log_text'):
            # GUI mode - add to log widget
            self.log_text.insert(tk.END, f"{formatted_message}\n")
            self.log_text.see(tk.END)
            self.root.update_idletasks()
        
        # Always log to console (useful for service logs)
        print(formatted_message)

    def setup_osc_handlers(self):
        """Setup OSC message handlers"""
        self.dispatcher.map("/fish/amplitude", self.set_amplitude)
        self.dispatcher.map("/fish/speed", self.set_speed)
        self.dispatcher.map("/fish/start", self.start_oscillation)
        self.dispatcher.map("/fish/stop", self.stop_oscillation)
        self.dispatcher.map("/fish/status", self.send_status)
        self.dispatcher.map("/fish/angle", self.set_angle)
        self.dispatcher.map("/fish/home", self.go_home)
        self.dispatcher.map("/fish/shutdown", self.shutdown_system)


    def move_to_position(self, position):
        self.packet_handler.write4ByteTxRx(self.port_handler, MOTOR_ID, ADDR_GOAL_POSITION, position)

    def set_motor_speed(self, speed):
        self.packet_handler.write2ByteTxRx(self.port_handler, MOTOR_ID, ADDR_MOVING_SPEED, speed)

    def set_amplitude(self, unused_addr, args):
        """OSC handler for setting amplitude"""
        try:
            amplitude = float(args)
            if amplitude <= 0:
                self.log_message("Amplitude must be positive")
                return
            self.amplitude_deg = amplitude
            # Update GUI only if available
            if self.has_gui and hasattr(self, 'amplitude_var'):
                self.amplitude_var.set(amplitude)
            self.log_message(f"Amplitude set to {amplitude} degrees (OSC)")
            self.update_config()
        except (ValueError, TypeError):
            self.log_message("Invalid amplitude value (OSC)")

    def set_speed(self, unused_addr, args):
        """OSC handler for setting speed"""
        try:
            speed = int(args)
            if speed <= 0:
                self.log_message("Speed must be positive")
                return
            self.speed = speed
            # Update GUI only if available
            if self.has_gui and hasattr(self, 'speed_var'):
                self.speed_var.set(speed)
            self.log_message(f"Speed set to {speed} steps/sec (OSC)")
            self.update_config()
        except (ValueError, TypeError):
            self.log_message("Invalid speed value (OSC)")

    def set_angle(self, unused_addr, args):
        """OSC handler for setting motor to specific angle"""
        try:
            angle = float(args)
            if angle < 0 or angle > 360:
                self.log_message("Angle must be between 0 and 360 degrees")
                return
            
            # Stop any ongoing oscillation
            if self.running:
                self.running = False
                self.log_message("Stopping oscillation to set angle")
            
            # Convert angle to dynamixel units and move motor
            angle_units = degrees_to_dxl_units(angle)
            self.move_to_position(angle_units)
            self.log_message(f"Motor moved to {angle} degrees (OSC)")
        except (ValueError, TypeError):
            self.log_message("Invalid angle value (OSC)")

    def go_home(self, unused_addr=None, args=None):
        """OSC handler for moving to home position (0 degrees)"""
        # Stop any ongoing oscillation
        if self.running:
            self.running = False
            self.log_message("Stopping oscillation to go home")
        
        # Move to home position (0 degrees)
        home_angle_units = degrees_to_dxl_units(0)
        self.move_to_position(home_angle_units)
        self.log_message("Motor moved to home position (0 degrees) (OSC)")

    def shutdown_system(self, unused_addr=None, args=None):
        """OSC handler for shutting down the Raspberry Pi"""
        self.log_message("Shutdown command received (OSC)")
        self.log_message("Stopping oscillation and cleaning up...")
        
        # Stop oscillation
        self.running = False
        
        # Move motor to home position
        self.move_to_position(self.zero_pos)
        
        # Disable motor torque
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)
        
        # Close port
        self.port_handler.closePort()
        
        # Shutdown OSC server
        if self.osc_available and hasattr(self, 'osc_server') and self.osc_server is not None:
            self.osc_server.shutdown()
        
        self.log_message("System shutting down in 3 seconds...")
        
        # Schedule system shutdown in a separate thread to avoid blocking
        def delayed_shutdown():
            time.sleep(3)
            self.log_message("Executing system shutdown now...")
            os.system("sudo shutdown -h now")
        
        threading.Thread(target=delayed_shutdown, daemon=True).start()

    def start_oscillation(self, unused_addr=None, args=None):
        """OSC handler for starting oscillation"""
        if self.running:
            self.log_message("Oscillation already running (OSC)")
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
        self.log_message("Oscillation started (OSC)")

    def stop_oscillation(self, unused_addr=None, args=None):
        """OSC handler for stopping oscillation"""
        if not self.running:
            self.log_message("Oscillation not running (OSC)")
            return
            
        self.running = False
        self.move_to_position(self.zero_pos)
        self.log_message("Oscillation stopped (OSC)")

    def send_status(self, unused_addr=None, args=None):
        """Print current status to console and log"""
        status = f"Status: running={self.running}, amplitude={self.amplitude_deg}, speed={self.speed}"
        print(status)
        self.log_message(status)

    def start_oscillation_gui(self):
        """GUI handler for starting oscillation"""
        try:
            amplitude_deg = self.amplitude_var.get()
            speed = self.speed_var.get()
        except (ValueError, tk.TclError):
            messagebox.showerror("Error", "Invalid amplitude or speed values")
            return

        if amplitude_deg <= 0 or speed <= 0:
            messagebox.showerror("Error", "Amplitude and speed must be positive")
            return

        self.amplitude_deg = amplitude_deg
        self.speed = speed
        
        amplitude_units = degrees_to_dxl_units(amplitude_deg)
        self.set_motor_speed(speed)
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
        self.log_message("Oscillation started (GUI)")

    def stop_oscillation_gui(self):
        """GUI handler for stopping oscillation"""
        self.running = False
        self.move_to_position(self.zero_pos)
        self.log_message("Oscillation stopped (GUI)")

    def save_settings_gui(self):
        """GUI handler for saving settings"""
        self.save_settings()

    def send_status_gui(self):
        """GUI handler for getting status"""
        self.send_status()

    def update_config(self):
        """Update JSON config file with current values"""
        config_file = "settings.json"
        try:
            # Load existing config
            config = load_config()
            if not config:
                config = {}
            
            # Update OSC settings only if OSC is available
            if self.osc_available:
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
            self.log_message("Configuration updated in settings.json")
        except Exception as e:
            self.log_message(f"Error updating config: {e}")

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
            if self.osc_available:
                if "osc" not in config:
                    config["osc"] = {}
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
            
            message = f"Settings saved to settings.json: amplitude={self.amplitude_deg}°, speed={self.speed}"
            print(message)
            self.log_message(message)
            
        except Exception as e:
            error_msg = f"Error saving settings: {e}"
            print(error_msg)
            self.log_message(error_msg)

    def cleanup(self):
        """Clean up resources"""
        self.running = False
        if self.osc_available and hasattr(self, 'osc_server') and self.osc_server is not None:
            self.osc_server.shutdown()
        self.packet_handler.write1ByteTxRx(self.port_handler, MOTOR_ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)
        self.port_handler.closePort()
        self.log_message("Cleanup complete.")

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
    
    parser = argparse.ArgumentParser(description="Single Motor Oscillator with OSC Control and GUI")
    parser.add_argument("--listen-ip", default=default_ip, 
                       help=f"IP address to listen for OSC messages (default: {default_ip})")
    parser.add_argument("--listen-port", type=int, default=default_port,
                       help=f"Port to listen for OSC messages (default: {default_port})")
    parser.add_argument("--no-gui", action="store_true",
                       help="Force run without GUI (console only)")
    parser.add_argument("--force-gui", action="store_true",
                       help="Force run with GUI (even if no display detected)")
    parser.add_argument("--no-auto-start", action="store_true",
                       help="Disable auto-start of oscillation in headless mode")
    
    args = parser.parse_args()
    
    # Save configuration if different from saved
    if not saved_config or args.listen_ip != saved_config.get("osc", {}).get("listen_ip") or args.listen_port != saved_config.get("osc", {}).get("listen_port"):
        save_config(args.listen_ip, args.listen_port)
    
    # Determine whether to use GUI mode
    use_gui = False
    if args.force_gui:
        use_gui = True
        print("GUI mode forced by --force-gui argument")
    elif args.no_gui:
        use_gui = False
        print("Console mode forced by --no-gui argument")
    else:
        # Automatic display detection
        print("Checking for display availability...")
        use_gui = is_display_connected()
    
    if use_gui:
        print("Starting in GUI mode")
    else:
        print("Starting in headless/console mode")
    
    try:
        if use_gui:
            # GUI mode
            root = tk.Tk()
            app = SingleMotorOscillator(root, args.listen_ip, args.listen_port)
            
            def on_closing():
                app.cleanup()
                root.destroy()
            
            root.protocol("WM_DELETE_WINDOW", on_closing)
            root.mainloop()
        else:
            # Console only mode
            oscillator = SingleMotorOscillator(None, args.listen_ip, args.listen_port)
            
            # Set auto-start preference
            oscillator.no_auto_start = args.no_auto_start
            
            # Auto-start if not disabled
            if not args.no_auto_start:
                oscillator.log_message("Auto-starting oscillation in headless mode...")
                oscillator.start_oscillation()
            
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nShutting down...")
            finally:
                oscillator.cleanup()
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()