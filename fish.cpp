#include <Dynamixel2Arduino.h>
#include <math.h>

// ---- XIAO ESP32-S3 pins ----
#define DXL_SERIAL  Serial1
#define DXL_TX_PIN  43
#define DXL_RX_PIN  44
#define DXL_DIR_PIN 5
#define BAUDRATE    57600

// ---- Motor Settings ----
const uint8_t MOTOR_ID = 1;
const float DXL_PROTOCOL_VERSION = 2.0;

// ---- Motion Parameters ----
const float CENTER_POS = 2048.0;   // Midpoint of Dynamixel (0–4095 range)
const float AMPLITUDE  = 400.0;    // Sine wave amplitude (adjust as desired)
const float SPEED      = 0.5;      // Oscillation frequency in Hz (cycles per second)

Dynamixel2Arduino dxl(DXL_SERIAL, DXL_DIR_PIN);

void setup() {
  Serial.begin(115200);
  DXL_SERIAL.begin(BAUDRATE, SERIAL_8N1, DXL_RX_PIN, DXL_TX_PIN);
  dxl.begin(BAUDRATE);
  dxl.setPortProtocolVersion(DXL_PROTOCOL_VERSION);

  if (!dxl.ping(MOTOR_ID)) {
    Serial.println("Motor not responding. Check wiring and ID.");
    while (true);
  }

  dxl.torqueOff(MOTOR_ID);
  dxl.setOperatingMode(MOTOR_ID, OP_POSITION);
  dxl.torqueOn(MOTOR_ID);

  // Optional: Set motion profiles
  dxl.writeControlTableItem(ControlTableItem::PROFILE_VELOCITY, MOTOR_ID, 400);
  dxl.writeControlTableItem(ControlTableItem::PROFILE_ACCELERATION, MOTOR_ID, 50);

  Serial.println("Sine wave motion initialized.");
}

void loop() {
  static unsigned long startTime = millis();

  // Compute elapsed time in seconds
  float t = (millis() - startTime) / 1000.0;

  // Generate sine wave position around center
  float position = CENTER_POS + AMPLITUDE * sin(2 * M_PI * SPEED * t);

  // Send goal position
  dxl.setGoalPosition(MOTOR_ID, (int)position);

  // Print for debugging (optional)
  Serial.print("Goal: ");
  Serial.println((int)position);

  delay(20);  // ~50 Hz update rate
}
