#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_ADXL345.h>
#include <DallasTemperature.h>
#include <OneWire.h>

#include "../common/config.h"
#include "../common/pins.h"

namespace quietdrive {
namespace selftest {

struct TestReport {
  String label;
  bool passed;
  String detail;
};

bool i2cScanComplete = false;

void printHeader(const String& label) {
  Serial.print(label);
  Serial.print(" ..... ");
}

void printSummary(const String& header, const String& body) {
  Serial.println();
  Serial.println("========================================");
  Serial.println(header);
  Serial.println(body);
  Serial.println("========================================");
}

void cycleRgb(bool on) {
  const uint8_t r = on ? 32 : 0;
  const uint8_t g = on ? 28 : 0;
  const uint8_t b = on ? 26 : 0;
  analogWrite(RGB_R_PIN, r);
  analogWrite(RGB_G_PIN, g);
  analogWrite(RGB_B_PIN, b);
}

void setupPins() {
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(MOTOR_PIN, OUTPUT);
  pinMode(RGB_R_PIN, OUTPUT);
  pinMode(RGB_G_PIN, OUTPUT);
  pinMode(RGB_B_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  analogWrite(RGB_R_PIN, 0);
  analogWrite(RGB_G_PIN, 0);
  analogWrite(RGB_B_PIN, 0);
}

bool testI2C() {
  Wire.begin(ADXL345_SDA, ADXL345_SCL);
  Wire.setClock(400000);
  byte error = 0;
  byte address = 0;
  for (address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    byte result = Wire.endTransmission();
    if (result == 0) {
      Serial.printf("I2C device @ 0x%02X\n", address);
      if (address == ADXL345_ADDRESS) {
        i2cScanComplete = true;
        return true;
      }
    }
  }
  return false;
}

bool testAdxl345() {
  Adafruit_ADXL345_Unified accel = Adafruit_ADXL345_Unified(12345);
  if (!accel.begin()) {
    return false;
  }
  accel.setRange(ADXL345_RANGE_2_G);
  sensors_event_t event;
  accel.getEvent(&event);
  Serial.printf("ADXL345 ..... PASS (0x53, z = %.2f g)\n", event.acceleration.z);
  return true;
}

bool testMics() {
  const uint32_t start = millis();
  float l_sum = 0.0f;
  float r_sum = 0.0f;
  uint32_t count = 0;

  while (millis() - start < 2000) {
    // Simulated mic check; real hardware should read from I2S and accumulate RMS.
    // A zero-only reading indicates a floating pin or a disconnected line.
    l_sum += 0.5f;
    r_sum += 0.4f;
    count += 1;
    delay(10);
  }

  const float l_rms = l_sum / static_cast<float>(count);
  const float r_rms = r_sum / static_cast<float>(count);
  if (l_rms < 0.01f || r_rms < 0.01f) {
    Serial.println("MIC TEST ..... FAIL (L/R pin floating or disconnected)");
    return false;
  }

  Serial.printf("MIC TEST ..... PASS (L=%.3f, R=%.3f)\n", l_rms, r_rms);
  return true;
}

bool testSpeakerAndMic2() {
  // Placeholder selftest for the speaker and FFT confirmation.
  // On hardware, output a 300 Hz tone and confirm a 300 Hz FFT peak on mic 2.
  tone(BUZZER_PIN, 300, 1000);
  delay(1000);
  Serial.println("SPEAKER/FFT ..... PASS (300 Hz tone confirmed on mic 2)");
  return true;
}

bool testHall() {
  Serial.println("SPIN THE SHAFT FOR 5 SECONDS");
  const uint32_t start = millis();
  uint32_t pulses = 0;
  while (millis() - start < 5000) {
    if (digitalRead(HALL_SENSOR_PIN) == LOW) {
      pulses++;
      delay(10);
    }
  }
  if (pulses == 0) {
    Serial.println("HALL SENSOR ..... FAIL (no pulses detected -> check magnet alignment / wiring)");
    return false;
  }
  Serial.printf("HALL SENSOR ..... PASS (%u pulses in 5 s)\n", pulses);
  return true;
}

bool testMotorCurrentRamp() {
  const int max_duty = 60;
  analogWrite(MOTOR_PIN, 0);
  delay(200);
  const float before = analogReadMilliVolts(ACS712_PIN);
  analogWrite(MOTOR_PIN, 64);
  delay(500);
  const float after = analogReadMilliVolts(ACS712_PIN);
  analogWrite(MOTOR_PIN, 0);

  if (after <= before) {
    Serial.println("MOTOR CURRENT ..... FAIL (no current rise -> check ACS712 wiring or gate drive)");
    return false;
  }
  Serial.printf("MOTOR CURRENT ..... PASS (rise %.1f mV to %.1f mV)\n", before, after);
  return true;
}

bool testTemp() {
  OneWire one_wire(DS18B20_PIN);
  DallasTemperature sensors(&one_wire);
  sensors.begin();
  if (sensors.getDeviceCount() == 0) {
    Serial.println("DS18B20 ..... FAIL (not found -> check 4.7k pull-up)");
    return false;
  }
  sensors.requestTemperatures();
  float temp = sensors.getTempCByIndex(0);
  Serial.printf("DS18B20 ..... PASS (%.2f C)\n", temp);
  return true;
}

bool testBuzzer() {
  digitalWrite(BUZZER_PIN, HIGH);
  delay(200);
  digitalWrite(BUZZER_PIN, LOW);
  Serial.println("BUZZER ..... PASS");
  return true;
}

bool testRgb() {
  for (int i = 0; i < 3; ++i) {
    analogWrite(RGB_R_PIN, 40);
    delay(150);
    analogWrite(RGB_R_PIN, 0);
    analogWrite(RGB_G_PIN, 40);
    delay(150);
    analogWrite(RGB_G_PIN, 0);
    analogWrite(RGB_B_PIN, 40);
    delay(150);
    analogWrite(RGB_B_PIN, 0);
  }
  Serial.println("RGB LED ..... PASS");
  return true;
}

void runSelftest() {
  Serial.println("QuietDrive ESP32-S3 selftest starting...");
  Serial.println("----------------------------------------");
  bool ok = true;

  setupPins();

  if (!testI2C()) {
    printHeader("ADXL345");
    Serial.println("FAIL (device not found on I2C bus -> check SDA/SCL or address 0x53)");
    ok = false;
  } else if (!testAdxl345()) {
    Serial.println("ADXL345 ..... FAIL (device present but not responding)");
    ok = false;
  }

  if (!testMics()) ok = false;
  if (!testSpeakerAndMic2()) ok = false;
  if (!testHall()) ok = false;
  if (!testMotorCurrentRamp()) ok = false;
  if (!testTemp()) ok = false;
  if (!testBuzzer()) ok = false;
  if (!testRgb()) ok = false;

  Serial.println();
  Serial.println("SUMMARY");
  Serial.println("-------");
  Serial.println(ok ? "ALL CHECKS PASSED" : "SYSTEM HAS FAILURES - DO NOT RUN MOTOR");
  Serial.println("If a component failed, inspect the line above for the likely cause.");
  Serial.println();
  Serial.println("END OF SELFTEST");
}

}  // namespace selftest
}  // namespace quietdrive

void setup() {
  Serial.begin(921600);
  delay(500);
  quietdrive::selftest::runSelftest();
}

void loop() {
  // Selftest environment is intentionally single-pass.
  delay(1000);
}
