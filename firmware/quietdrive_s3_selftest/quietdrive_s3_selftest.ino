/*
  QuietDrive ESP32-S3 self-test: checks every sensor and actuator, prints PASS / FAIL / SKIP / WARN.
  Board "ESP32S3 Dev Module", Serial Monitor 921600, line ending "Newline". Pins = quietdrive_s3.ino.

  Order: I2S + both mics -> speaker -> mics (also checks which slot is which) -> ADXL345 ->
         ACS712 zero -> DS18B20 -> buzzer / RGB (by eye) -> motor + Hall + current (only if you type y).
  Send 'r' to run again.
*/
#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#include "driver/i2s_std.h"
#if __has_include(<DallasTemperature.h>)
#include <OneWire.h>
#include <DallasTemperature.h>
#define HAVE_DS18B20 1
#else
#define HAVE_DS18B20 0
#endif

const int PIN_BCLK = 15, PIN_WS = 16, PIN_DOUT = 17, PIN_DIN = 14;
const int PIN_SDA = 8, PIN_SCL = 9, PIN_HALL = 5, PIN_CURRENT = 4, PIN_DS18B20 = 6;
const int PIN_MOTOR = 7, PIN_BUZZER = 21, PIN_R = 39, PIN_G = 40, PIN_B = 41;
const int FS = 16000, N = 64, MAGNETS = 1, TEST_DUTY = 40;
const float ACS_MV_PER_A = 185.0f, ACS_DIVIDER = 2.0f / 3.0f;

struct Result { const char* name; const char* verdict; char detail[96]; };
Result res[16]; int nres = 0;
void report(const char* name, const char* verdict, const char* fmt, ...) {
  Result& r = res[nres < 16 ? nres++ : 15]; r.name = name; r.verdict = verdict;
  va_list ap; va_start(ap, fmt); vsnprintf(r.detail, sizeof(r.detail), fmt, ap); va_end(ap);
  Serial.printf("  %-22s %-5s %s\n", name, verdict, r.detail);
}

// ---------------- I2S full duplex (same setup as the main firmware) ----------------
i2s_chan_handle_t txh, rxh; bool i2sOk = false;
int32_t txb[2 * N], rxb[2 * N];
bool i2sStart() {
  i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  cc.dma_desc_num = 3; cc.dma_frame_num = N; cc.auto_clear = true;
  if (i2s_new_channel(&cc, &txh, &rxh) != ESP_OK) return false;
  i2s_std_config_t sc = {
    .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(FS),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
    .gpio_cfg = { .mclk = I2S_GPIO_UNUSED, .bclk = (gpio_num_t)PIN_BCLK, .ws = (gpio_num_t)PIN_WS,
                  .dout = (gpio_num_t)PIN_DOUT, .din = (gpio_num_t)PIN_DIN,
                  .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false} },
  };
  if (i2s_channel_init_std_mode(txh, &sc) != ESP_OK || i2s_channel_init_std_mode(rxh, &sc) != ESP_OK) return false;
  return i2s_channel_enable(txh) == ESP_OK && i2s_channel_enable(rxh) == ESP_OK;
}

// play a tone (amp 0 = silence) for 'sec' and measure both mics: rms, peak, lock-in amplitude at f
struct MicStat { double rms[2], tone[2], peak[2], mean[2]; bool stuck[2]; };
MicStat runAudio(float f, float amp, float sec) {
  MicStat m = {};
  double re[2] = {0, 0}, im[2] = {0, 0}, ss[2] = {0, 0}, sm[2] = {0, 0}; long cnt = 0;
  int32_t first[2] = {0, 0}; bool same[2] = {true, true};
  double ph = 0, step = 2 * PI * f / FS;
  int blocks = sec * FS / N, skip = FS / 10 / N;
  for (int b = 0; b < blocks; b++) {
    for (int i = 0; i < N; i++) { int32_t s = (int32_t)(amp * cos(ph + step * i) * 2147483000.0); txb[2 * i] = s; txb[2 * i + 1] = s; }
    size_t n;
    i2s_channel_write(txh, txb, sizeof(txb), &n, portMAX_DELAY);
    i2s_channel_read(rxh, rxb, sizeof(rxb), &n, portMAX_DELAY);
    if (b >= skip)
      for (int i = 0; i < N; i++)
        for (int c = 0; c < 2; c++) {
          int32_t raw = rxb[2 * i + c] >> 8; float v = raw / 8388608.0f;
          if (!cnt && !i) first[c] = raw; else if (raw != first[c]) same[c] = false;
          ss[c] += v * v; sm[c] += v; m.peak[c] = fmax(m.peak[c], fabs(v));
          re[c] += v * cos(ph + step * i); im[c] -= v * sin(ph + step * i);
          if (c == 1) cnt++;
        }
    ph = fmod(ph + step * N, 2 * PI);
  }
  for (int c = 0; c < 2; c++) {
    m.mean[c] = sm[c] / cnt; m.rms[c] = sqrt(fmax(0.0, ss[c] / cnt - m.mean[c] * m.mean[c]));
    m.tone[c] = 2 * sqrt(re[c] * re[c] + im[c] * im[c]) / cnt; m.stuck[c] = same[c];
  }
  return m;
}
double db(double v) { return 20 * log10(v + 1e-12); }

void testAudio() {
  i2sOk = i2sOk || i2sStart();
  report("I2S full duplex", i2sOk ? "PASS" : "FAIL", i2sOk ? "BCLK15 WS16 DOUT17 DIN14, 16 kHz 32-bit stereo" : "driver init failed");
  if (!i2sOk) return;
  MicStat q = runAudio(1000, 0, 0.6f);
  const char* nm[2] = {"Reference mic (L)", "Error mic (R)"};
  for (int c = 0; c < 2; c++) {
    bool ok = !q.stuck[c] && q.rms[c] > 1e-6 && q.peak[c] < 0.99;
    report(nm[c], ok ? "PASS" : "FAIL", "%s noise %.1f dBFS, peak %.3f",
           q.stuck[c] ? "STUCK (no data: check SD/L-R/3V3)," : "", db(q.rms[c]), q.peak[c]);
  }
  MicStat t = runAudio(1000, 0.3f, 0.6f);
  double rise[2] = {db(t.tone[0]) - db(q.tone[0] + 1e-9), db(t.tone[1]) - db(q.tone[1] + 1e-9)};
  bool heard = rise[1] > 20;
  report("Speaker -> error mic", heard ? "PASS" : "FAIL", "1 kHz tone +%.0f dB at error mic, +%.0f dB at ref mic", rise[1], rise[0]);
  if (heard || rise[0] > 20)
    report("Mic slot order", t.tone[1] >= t.tone[0] ? "PASS" : "WARN",
           t.tone[1] >= t.tone[0] ? "error mic (R slot) is nearer the speaker, as expected"
                                  : "ref mic hears the speaker louder: L/R straps swapped, or speaker is near the motor mic");
}

// ---------------- ADXL345 ----------------
const uint8_t ADXL = 0x53;
void aw(uint8_t r, uint8_t v) { Wire.beginTransmission(ADXL); Wire.write(r); Wire.write(v); Wire.endTransmission(); }
int ar(uint8_t r) { Wire.beginTransmission(ADXL); Wire.write(r); if (Wire.endTransmission(false)) return -1; if (Wire.requestFrom(ADXL, (uint8_t)1) != 1) return -1; return Wire.read(); }
void testAdxl() {
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  int id = ar(0x00);
  if (id != 0xE5) { report("ADXL345", "FAIL", "DEVID 0x%02X (want 0xE5) at 0x53: check SDA8/SCL9, CS->3V3, SDO->GND", id & 0xFF); return; }
  aw(0x2D, 0); aw(0x2C, 0x0E); aw(0x31, 0x09); aw(0x38, 0x80 | 16); aw(0x2D, 0x08);
  delay(50); while (ar(0x39) & 0x3F) { Wire.beginTransmission(ADXL); Wire.write(0x32); Wire.endTransmission(false); Wire.requestFrom(ADXL, (uint8_t)6); while (Wire.available()) Wire.read(); }
  long cnt = 0; double sx = 0, sy = 0, sz = 0; bool overflow = false;
  uint32_t t0 = millis();
  while (millis() - t0 < 1000) {
    int n = ar(0x39) & 0x3F; if (n >= 32) overflow = true;
    for (int i = 0; i < n; i++) {
      Wire.beginTransmission(ADXL); Wire.write(0x32); Wire.endTransmission(false);
      if (Wire.requestFrom(ADXL, (uint8_t)6) != 6) continue;
      uint8_t b[6]; for (int j = 0; j < 6; j++) b[j] = Wire.read();
      sx += (int16_t)(b[0] | b[1] << 8) * 0.0039; sy += (int16_t)(b[2] | b[3] << 8) * 0.0039; sz += (int16_t)(b[4] | b[5] << 8) * 0.0039; cnt++;
    }
    delay(2);
  }
  double g = cnt ? sqrt(sx * sx + sy * sy + sz * sz) / cnt : 0;
  report("ADXL345 rate", cnt > 1400 && cnt < 1800 && !overflow ? "PASS" : "FAIL", "%ld samples/s (want ~1600)%s", cnt, overflow ? ", FIFO overflowed" : "");
  report("ADXL345 gravity", g > 0.85 && g < 1.15 ? "PASS" : "FAIL", "|g| = %.3f g (want ~1.0 at rest)", g);
}

// ---------------- ACS712 / DS18B20 ----------------
float zeroMv = 0;
void testCurrentZero() {
  analogReadResolution(12); analogSetPinAttenuation(PIN_CURRENT, ADC_11db);
  double s = 0; for (int i = 0; i < 400; i++) { s += analogReadMilliVolts(PIN_CURRENT); delayMicroseconds(250); }
  zeroMv = s / 400;
  float want = 2500 * ACS_DIVIDER;
  report("ACS712 zero", fabs(zeroMv - want) < 200 ? "PASS" : "FAIL", "%.0f mV at 0 A (want ~%.0f: 5 V sensor via 2/3 divider)", zeroMv, want);
}
void testTemp() {
#if HAVE_DS18B20
  OneWire ow(PIN_DS18B20); DallasTemperature ds(&ow);
  ds.begin();
  if (!ds.getDeviceCount()) { report("DS18B20", "FAIL", "no sensor on GPIO6 (4.7k pull-up to 3V3?)"); return; }
  ds.requestTemperatures(); float t = ds.getTempCByIndex(0);
  report("DS18B20", t > -10 && t < 90 && t != 85 ? "PASS" : "FAIL", "%.2f C", t);
#else
  report("DS18B20", "SKIP", "install the OneWire + DallasTemperature libraries");
#endif
}

void testOutputs() {
  ledcAttachChannel(PIN_BUZZER, 2000, 10, 6); ledcWriteTone(PIN_BUZZER, 2000); delay(150); ledcWriteTone(PIN_BUZZER, 0);
  int pins[3] = {PIN_R, PIN_G, PIN_B}; const int ch[3] = {2, 3, 4};
  for (int i = 0; i < 3; i++) ledcAttachChannel(pins[i], 5000, 8, ch[i]);
  for (int i = 0; i < 3; i++) { for (int j = 0; j < 3; j++) ledcWrite(pins[j], j == i ? 120 : 0); delay(300); }
  for (int j = 0; j < 3; j++) ledcWrite(pins[j], 0);
  report("Buzzer + RGB", "INFO", "you should have heard a beep and seen red, green, blue");
}

// ---------------- motor + Hall + current under load ----------------
volatile uint32_t pulses = 0, lastUs = 0;
void IRAM_ATTR hallIsr() { uint32_t n = micros(); if (n - lastUs > 300) { pulses = pulses + 1; lastUs = n; } }
void testMotor() {
  Serial.printf("\nMotor test spins the motor at %d %% for 3 s. Type y + Enter within 8 s to run it (anything else skips).\n", TEST_DUTY);
  uint32_t t0 = millis(); String s;
  while (millis() - t0 < 8000 && !Serial.available()) delay(10);
  if (Serial.available()) { s = Serial.readStringUntil('\n'); s.trim(); }
  if (s != "y") { report("Motor PWM", "SKIP", "not confirmed"); report("Hall rpm", "SKIP", ""); report("ACS712 load", "SKIP", ""); return; }
  pinMode(PIN_HALL, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_HALL), hallIsr, FALLING);
  ledcAttachChannel(PIN_MOTOR, 20000, 10, 0);
  ledcWrite(PIN_MOTOR, TEST_DUTY * 1023 / 100);
  delay(1500);                                           // spin up
  pulses = 0; double si = 0; long ni = 0; t0 = millis();
  while (millis() - t0 < 1500) { si += (analogReadMilliVolts(PIN_CURRENT) - zeroMv) / (ACS_MV_PER_A * ACS_DIVIDER); ni++; delay(1); }
  uint32_t p = pulses;
  ledcWrite(PIN_MOTOR, 0);
  float rpm = p / 1.5f / MAGNETS * 60, amps = si / ni;
  report("Motor PWM", "INFO", "GPIO7 20 kHz %d %% for 3 s - did it spin?", TEST_DUTY);
  report("Hall rpm", rpm > 300 ? "PASS" : "FAIL", "%.0f rpm (%lu pulses in 1.5 s, MAGNETS=%d)%s", rpm, (unsigned long)p, MAGNETS,
         p == 0 ? ": check A3144 5 V supply, 10k pull-up to 3V3, magnet pole/gap" : "");
  report("ACS712 load", fabs(amps) > 0.05 ? "PASS" : "WARN", "%.3f A while running (expect > 0.05 A)", amps);
  detachInterrupt(digitalPinToInterrupt(PIN_HALL));
}

void runAll() {
  nres = 0;
  Serial.println("\n=== QuietDrive ESP32-S3 self-test ===  (keep the motor OFF and the room quiet)");
  testAudio(); testAdxl(); testCurrentZero(); testTemp(); testOutputs(); testMotor();
  int fail = 0, warn = 0;
  Serial.println("\n---------------- summary ----------------");
  for (int i = 0; i < nres; i++) {
    Serial.printf("  %-5s %s\n", res[i].verdict, res[i].name);
    if (!strcmp(res[i].verdict, "FAIL")) fail++;
    if (!strcmp(res[i].verdict, "WARN")) warn++;
  }
  Serial.printf("%s  (%d fail, %d warn). Send 'r' to run again.\n", fail ? "SELF-TEST FAILED" : "SELF-TEST PASSED", fail, warn);
}

void setup() { Serial.begin(921600); delay(1500); runAll(); }
void loop() {
  if (Serial.available()) { String s = Serial.readStringUntil('\n'); s.trim(); if (s == "r") runAll(); }
  delay(20);
}
