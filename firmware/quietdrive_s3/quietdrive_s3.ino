/*
  QuietDrive - ESP32-S3 hardware edition (one board: sensing, fault-aware ANC, motor drive)

  Board: "ESP32S3 Dev Module" (Arduino core 3.x). Plug USB into the board's UART port
  (or enable "USB CDC On Boot"). Newline-delimited JSON at 921600 baud, see docs/protocol.md.
  Wiring, power and calibration: docs/hardware.md.

  Cores
    core 1  audioTask   hard real-time, 64-frame (4 ms) blocks: I2S full duplex, 2-channel
                        narrowband FxLMS, lock-in levels, ring buffers. No Serial, no heap,
                        no per-sample trig (reference by rotation, 2 trig calls per channel per block).
    core 0  loop()      ADXL345 FIFO, ACS712, DS18B20, motor PWM, features, JSON in/out.

  ANC (algorithm reused from the proven classic-ESP32 sketch esp32_anc_.ino)
    reference  : synthesised from the Hall shaft speed, f1 = shaft x WHINE_ORDER and f2 = 2 x f1,
                 recomputed EVERY block, so the ANC follows the motor as it speeds up or slows down.
    error      : error mic (cabin / driver ear). Feedback adaptation by narrowband FxLMS.
    path       : speaker->error-mic gain/phase measured as a table over +-20 % around the
                 operating frequency, with the fixed I2S delay estimated and removed, so the
                 path stays correct while the frequency moves ("anc_calibrate", motor stopped).
    protected  : the 1x shaft tone (fault evidence) and any tone the backend sends in
                 inf.protected_hz are never cancelled: a channel within PROTECT_BW_HZ of one is muted.
    guards     : weight clamp at the output limit, louder-than-baseline reset, motor-stopped gate.
*/
#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
#include <math.h>
#include "driver/i2s_std.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"

#if __has_include(<DallasTemperature.h>)
#include <OneWire.h>
#include <DallasTemperature.h>
#define HAVE_DS18B20 1
#else
#define HAVE_DS18B20 0
#endif

// ======================================================================= pins
const int PIN_BCLK = 15, PIN_WS = 16, PIN_DOUT = 17, PIN_DIN = 14;   // one I2S port, full duplex
const int PIN_SDA = 8, PIN_SCL = 9;                                  // ADXL345
const int PIN_HALL = 5;                                              // A3144, open collector
const int PIN_CURRENT = 4;                                           // ACS712-5A via divider
const int PIN_DS18B20 = 6;
const int PIN_MOTOR = 7;                                             // LEDC -> MOSFET gate driver
const int PIN_BUZZER = 21;
const int PIN_R = 39, PIN_G = 40, PIN_B = 41;

// ======================================================================= config
const char* VID = "QD-01";
const uint32_t BAUD = 921600;
const int FS = 16000;                 // audio sample rate (same as bridge/common.py)
const int N = 64;                     // frames per block = 4 ms
const int MAGNETS = 1;                // magnets on the shaft (pulses per revolution)
const uint32_t HALL_DEBOUNCE_US = 300;
const uint32_t HALL_TIMEOUT_US = 500000;
const float STOPPED_RPM = 300.0f;     // below this: motor stopped, no ANC
float DEFAULT_WHINE_ORDER = 21.0f;    // = WHINE_ORDER in bridge/models/calibration.json (phone calibration)
const float DEFAULT_WHINE_HZ = 1250.0f;   // calibration centre if the motor has never run
const float PROTECT_BW_HZ = 8.0f;     // a channel this close to a protected tone is muted
const bool RGB_COMMON_ANODE = false;
const float ACS_MV_PER_A = 185.0f;    // ACS712-5A
const float ACS_DIVIDER = 2.0f / 3.0f;// 5 V output -> 3.3 V ADC (e.g. 10k over 20k)
const uint32_t PWM_BASE_HZ = 20000;   // fixed mode
const float PWM_SPREAD_FRAC = 0.15f;      // spread mode: +-15 % random hop every 2 ms
const int PWM_BITS = 10;

// ======================================================================= helpers
static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
static inline float wrapPi2(float p) { const float T = 6.2831853f; while (p >= T) p -= T; while (p < 0) p += T; return p; }

// ======================================================================= Hall (ISR)
volatile uint32_t hallLastUs = 0, hallPulses = 0, hallSum = 0;
volatile uint32_t hallDt[MAGNETS];
volatile uint8_t hallIdx = 0, hallValid = 0;

void IRAM_ATTR hallIsr() {
  uint32_t now = micros(), dt = now - hallLastUs;
  if (hallLastUs && dt < HALL_DEBOUNCE_US) return;
  hallLastUs = now;
  hallPulses = hallPulses + 1;
  if (dt > 2 * HALL_TIMEOUT_US) { hallValid = 0; hallSum = 0; for (int i = 0; i < MAGNETS; i++) hallDt[i] = 0; return; }
  hallSum += dt - hallDt[hallIdx];          // sum over one full revolution cancels magnet spacing errors
  hallDt[hallIdx] = dt;
  hallIdx = (hallIdx + 1) % MAGNETS;
  if (hallValid < MAGNETS) hallValid = hallValid + 1;
}

float hallShaftHz() {                       // safe from either core (no locks, one 32-bit snapshot each)
  uint32_t last = hallLastUs, sum = hallSum; uint8_t valid = hallValid;
  if (valid < MAGNETS || sum == 0) return 0;
  uint32_t age = micros() - last;
  if (age > HALL_TIMEOUT_US || age > 3 * sum) return 0;
  return 1e6f / (float)sum;
}

// ======================================================================= shared core1 <-> core0
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

const int REF_RING = 16384, ERR_RING = 8192, BLK_RING = 512;      // powers of two
float* refRing;                       // reference mic, 16 kHz (1.02 s)
float* errRing;                       // error mic
volatile uint32_t audioCount = 0;     // samples written so far (both rings)
int64_t blockUs[BLK_RING];            // esp_timer time when each block was read

enum CmdType : uint8_t { C_ANC, C_MU, C_VMAX, C_ORDER, C_CAL, C_CAL_ABORT, C_RESET };
struct Cmd { CmdType type; float a; };
QueueHandle_t cmdQ;

const int MAX_PROT = 4;
volatile float protOrders[MAX_PROT];  // extra protected tones as multiples of shaft speed (from backend inf)
volatile int protCount = 0;

enum CalState : uint8_t { CAL_NONE = 0, CAL_RUNNING, CAL_OK, CAL_FAILED };
struct AncStatus {
  bool enabled, active[2], atLimit, outOfRange[2], stopped;
  float f[2], red[2], errDb[2], out, mu, vmax, order, shaftHz, tauMs, pathMag[2], loadPct;
  uint8_t cal, calPct;
  uint32_t guardResets, blocks;
  char msg[64];
};
AncStatus st;                         // written by core 1 inside mux, copied by core 0 inside mux

// ======================================================================= audio state (core 1 only)
i2s_chan_handle_t txh, rxh;
int32_t txbuf[2 * N], rxbuf[2 * N];
float refm[N], errm[N];

const int K = 17;                     // path table points per channel, +-20 % around the centre
const float SPAN = 0.20f;
struct PathTable { float f0, f1; float re[K], im[K]; bool ok; };
PathTable path[2];
float tau = 0;                        // fixed delay removed from the table (s)
float calCenter = DEFAULT_WHINE_HZ;
bool ancOn = false;
float mu = 0.01f, vmax = 0.6f, order = 21.0f;
float w0[2] = {0, 0}, w1[2] = {0, 0};
float ph[2] = {0, 0};
float cs[2][N], sn[2][N];
float lastRunF = 0;                   // last whine frequency seen while running (calibration centre)

void makeRef(int k, float ph0, float step) {      // 2 trig calls per block, rest by rotation
  float c = cosf(ph0), s = sinf(ph0), dc = cosf(step), ds = sinf(step);
  for (int i = 0; i < N; i++) { cs[k][i] = c; sn[k][i] = s; float c2 = c * dc - s * ds; s = s * dc + c * ds; c = c2; }
}

void i2sStart() {
  i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  cc.dma_desc_num = 3;                // short, fixed latency (ESP_I2S uses 6 x 240 frames = 90 ms)
  cc.dma_frame_num = N;
  cc.auto_clear = true;
  ESP_ERROR_CHECK(i2s_new_channel(&cc, &txh, &rxh));     // TX + RX on one port: one shared clock
  i2s_std_config_t sc = {
    .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(FS),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED, .bclk = (gpio_num_t)PIN_BCLK, .ws = (gpio_num_t)PIN_WS,
      .dout = (gpio_num_t)PIN_DOUT, .din = (gpio_num_t)PIN_DIN,
      .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
    },
  };
  ESP_ERROR_CHECK(i2s_channel_init_std_mode(txh, &sc));
  ESP_ERROR_CHECK(i2s_channel_init_std_mode(rxh, &sc));
  memset(txbuf, 0, sizeof(txbuf));
  size_t loaded;
  for (int i = 0; i < 3; i++) i2s_channel_preload_data(txh, txbuf, sizeof(txbuf), &loaded);
  ESP_ERROR_CHECK(i2s_channel_enable(txh));
  ESP_ERROR_CHECK(i2s_channel_enable(rxh));
}

// one block out (mono -> both slots), one block in: left slot = reference mic, right = error mic
void ioBlock(const float* out) {
  for (int i = 0; i < N; i++) {
    int32_t s = (int32_t)(clampf(out[i], -1.0f, 1.0f) * 2147483000.0f);
    txbuf[2 * i] = s; txbuf[2 * i + 1] = s;
  }
  size_t n;
  i2s_channel_write(txh, txbuf, sizeof(txbuf), &n, portMAX_DELAY);
  i2s_channel_read(rxh, rxbuf, sizeof(rxbuf), &n, portMAX_DELAY);
  for (int i = 0; i < N; i++) {
    refm[i] = (float)(rxbuf[2 * i] >> 8) / 8388608.0f;       // 24-bit data in 32-bit slots
    errm[i] = (float)(rxbuf[2 * i + 1] >> 8) / 8388608.0f;
  }
}

// path S(f) = table residual (linear interpolation) x exp(-j 2 pi f tau)
bool pathAt(int k, float f, float& Sr, float& Si) {
  const PathTable& p = path[k];
  if (!p.ok || f < p.f0 || f > p.f1) return false;
  float x = (f - p.f0) / (p.f1 - p.f0) * (K - 1);
  int i = (int)x; if (i >= K - 1) i = K - 2;
  float a = x - i;
  float rr = p.re[i] + a * (p.re[i + 1] - p.re[i]), ri = p.im[i] + a * (p.im[i + 1] - p.im[i]);
  float th = -6.2831853f * f * tau, c = cosf(th), s = sinf(th);
  Sr = rr * c - ri * s; Si = rr * s + ri * c;
  return true;
}

void setMsg(const char* m) { strncpy(st.msg, m, sizeof(st.msg) - 1); st.msg[sizeof(st.msg) - 1] = 0; }

void savePath() {
  Preferences pr; pr.begin("qd_anc", false);
  pr.putBytes("path", path, sizeof(path)); pr.putFloat("tau", tau); pr.putFloat("center", calCenter);
  pr.end();
}
void loadPath() {
  Preferences pr; pr.begin("qd_anc", true);
  if (pr.getBytesLength("path") == sizeof(path)) { pr.getBytes("path", path, sizeof(path)); tau = pr.getFloat("tau", 0); calCenter = pr.getFloat("center", DEFAULT_WHINE_HZ); }
  else { path[0].ok = path[1].ok = false; }
  order = pr.getFloat("order", DEFAULT_WHINE_ORDER);
  pr.end();
}
void saveOrder() { Preferences pr; pr.begin("qd_anc", false); pr.putFloat("order", order); pr.end(); }

// ---------------- secondary-path calibration (motor stopped), run block by block ----------------
// Sequence: for channel k (centre fc_k = fc x (k+1)): 2 delay probes (fc, fc+4 Hz, long), then K
// table points over +-SPAN. Each point: SETTLE blocks of tone, then INTEG blocks lock-in.
const float CAL_A = 0.3f;
const int SETTLE = 15, INTEG = 35, PROBE_INTEG = 90;
struct Cal { bool on; int k, pt, blk; float f; double re, im; long cnt; float pr[2], pi[2]; } cal;
const int CAL_STEPS = 2 + K;          // per channel

float calFreq(int k, int pt) {
  float fc = calCenter * (k + 1);
  if (pt == 0) return fc;
  if (pt == 1) return fc + 4.0f;
  return fc * (1.0f - SPAN + 2.0f * SPAN * (pt - 2) / (K - 1));
}

void calStart(float fc) {
  calCenter = fc > 50 ? fc : (lastRunF > 50 ? lastRunF : calCenter);
  cal = {}; cal.on = true; cal.f = calFreq(0, 0);
  ancOn = false; w0[0] = w1[0] = w0[1] = w1[1] = 0;
  path[0].ok = path[1].ok = false;
  st.cal = CAL_RUNNING; st.calPct = 0; setMsg("calibrating speaker->error mic path");
}

void calFinishChannel(int k) {
  // delay from the probe pair: phase(fc+4) - phase(fc) = -2 pi 4 tau
  float p0 = atan2f(cal.pi[0], cal.pr[0]), p1 = atan2f(cal.pi[1], cal.pr[1]);
  float d = p1 - p0; while (d > PI) d -= 2 * PI; while (d < -PI) d += 2 * PI;
  float tauK = -d / (2 * PI * 4.0f);
  if (k == 0) tau = tauK;                       // channel 0 defines tau; channel 1 refines below
  // remove the delay, then fit the remaining linear phase (least squares) and fold it into tau
  PathTable& p = path[k];
  float ph_[K], fx[K]; float prev = 0, off = 0;
  for (int i = 0; i < K; i++) {
    fx[i] = calFreq(k, i + 2);
    float th = 2 * PI * fx[i] * tau, c = cosf(th), s = sinf(th);
    float rr = p.re[i] * c - p.im[i] * s, ri = p.re[i] * s + p.im[i] * c;
    float a = atan2f(ri, rr);
    if (i) { float dd = a - prev; while (dd > PI) { off -= 2 * PI; dd -= 2 * PI; } while (dd < -PI) { off += 2 * PI; dd += 2 * PI; } }
    prev = a; ph_[i] = a + off;
  }
  float mf = 0, mp = 0; for (int i = 0; i < K; i++) { mf += fx[i]; mp += ph_[i]; } mf /= K; mp /= K;
  float sxy = 0, sxx = 0; for (int i = 0; i < K; i++) { sxy += (fx[i] - mf) * (ph_[i] - mp); sxx += (fx[i] - mf) * (fx[i] - mf); }
  float slope = sxx > 0 ? sxy / sxx : 0;
  if (k == 0) tau += -slope / (2 * PI);         // channel 1 then uses the same refined tau
  float minMag = 1e9;
  for (int i = 0; i < K; i++) {                 // store residual R = S exp(+j 2 pi f tau)
    float th = 2 * PI * fx[i] * tau, c = cosf(th), s = sinf(th);
    float rr = p.re[i] * c - p.im[i] * s, ri = p.re[i] * s + p.im[i] * c;
    p.re[i] = rr; p.im[i] = ri;
    float m = sqrtf(rr * rr + ri * ri); if (m < minMag) minMag = m;
  }
  p.f0 = fx[0]; p.f1 = fx[K - 1];
  p.ok = minMag > 0.002f;                       // same threshold as the proven sketch
  st.pathMag[k] = minMag;
}

// one calibration block: returns the output to play
void calBlock(float* out) {
  float step = 2 * PI * cal.f / FS;
  makeRef(0, ph[0], step);
  for (int i = 0; i < N; i++) out[i] = CAL_A * cs[0][i];
  ph[0] = wrapPi2(ph[0] + step * N);
}
void calAfterIo() {
  int integ = cal.pt < 2 ? PROBE_INTEG : INTEG;
  if (cal.blk >= SETTLE) for (int i = 0; i < N; i++) { cal.re += errm[i] * cs[0][i]; cal.im -= errm[i] * sn[0][i]; cal.cnt++; }
  if (++cal.blk < SETTLE + integ) return;
  float Sr = 2 * cal.re / cal.cnt / CAL_A, Si = 2 * cal.im / cal.cnt / CAL_A;
  if (cal.pt < 2) { cal.pr[cal.pt] = Sr; cal.pi[cal.pt] = Si; }
  else { path[cal.k].re[cal.pt - 2] = Sr; path[cal.k].im[cal.pt - 2] = Si; }
  cal.blk = 0; cal.re = cal.im = 0; cal.cnt = 0;
  if (++cal.pt >= CAL_STEPS) {
    calFinishChannel(cal.k);
    cal.pt = 0;
    if (++cal.k >= 2) {
      cal.on = false;
      bool ok = path[0].ok && path[1].ok;
      st.cal = ok ? CAL_OK : CAL_FAILED; st.calPct = 100;
      st.tauMs = tau * 1000;
      setMsg(ok ? "path OK - start the motor, then ANC on" : "path too weak: speaker closer to error mic / louder");
      return;                                   // core 0 saves the table to flash

    }
  }
  st.calPct = (uint8_t)(100 * (cal.k * CAL_STEPS + cal.pt) / (2 * CAL_STEPS));
  cal.f = calFreq(cal.k, cal.pt);
}

// ---------------- the real-time task ----------------
void handleCmds() {
  Cmd c;
  while (xQueueReceive(cmdQ, &c, 0) == pdTRUE) {
    switch (c.type) {
      case C_ANC: ancOn = c.a > 0.5f && path[0].ok; for (int k = 0; k < 2; k++) w0[k] = w1[k] = 0; break;
      case C_MU: mu = clampf(c.a, 0.0005f, 0.1f); break;
      case C_VMAX: vmax = clampf(c.a, 0.0f, 1.0f); break;
      case C_ORDER: order = c.a; for (int k = 0; k < 2; k++) w0[k] = w1[k] = 0; break;
      case C_CAL: calStart(c.a); break;
      case C_CAL_ABORT: cal.on = false; st.cal = CAL_NONE; setMsg("calibration aborted - recalibrate before ANC"); break;
      case C_RESET: for (int k = 0; k < 2; k++) w0[k] = w1[k] = 0; break;
    }
  }
}

bool nearProtected(float f, float shaft) {
  if (fabsf(f - shaft) < PROTECT_BW_HZ) return true;                  // the 1x shaft tone, always
  int n = protCount;
  for (int i = 0; i < n && i < MAX_PROT; i++) if (fabsf(f - protOrders[i] * shaft) < PROTECT_BW_HZ) return true;
  return false;
}

void audioTask(void*) {
  i2sStart();
  static float out[N];
  float shaftS = 0;
  // 0.5 s lock-in windows per channel (error and reference mic), for levels and the guard
  double eRe[2] = {0}, eIm[2] = {0}, rRe[2] = {0}, rIm[2] = {0}; int aN = 0;
  float baseRatio[2] = {NAN, NAN}, red[2] = {0, 0}, errDb[2] = {-120, -120};
  int bad[2] = {0, 0};
  uint32_t blocks = 0, busyUs = 0, winUs = 0;
  for (;;) {
    handleCmds();
    int64_t t0 = esp_timer_get_time();

    float sh = hallShaftHz();
    shaftS = (sh <= 0) ? 0 : (shaftS <= 0 ? sh : shaftS + 0.3f * (sh - shaftS));
    bool stopped = shaftS * 60 < STOPPED_RPM;
    float f[2] = {shaftS * order, 2 * shaftS * order};
    if (!stopped) lastRunF = f[0];

    bool act[2] = {false, false}, oor[2] = {false, false};
    float S[2][2] = {{0, 0}, {0, 0}};
    if (cal.on) {
      calBlock(out);
    } else {
      for (int k = 0; k < 2; k++) {
        bool inRange = pathAt(k, f[k], S[k][0], S[k][1]);
        oor[k] = ancOn && !stopped && !inRange;
        act[k] = ancOn && !stopped && inRange && order >= 2 && f[k] < 0.45f * FS && !nearProtected(f[k], shaftS);
        float step = 2 * PI * f[k] / FS;
        makeRef(k, ph[k], step);
        ph[k] = wrapPi2(ph[k] + step * N);
        if (!act[k]) { w0[k] *= 0.9f; w1[k] *= 0.9f; }                 // fade out, no click
      }
      for (int i = 0; i < N; i++) {
        float y = 0;
        for (int k = 0; k < 2; k++) y += clampf(w0[k] * cs[k][i] + w1[k] * sn[k][i], -vmax, vmax);
        out[i] = clampf(y, -vmax, vmax);
      }
    }

    int64_t tIo = esp_timer_get_time();
    ioBlock(out);
    int64_t tRead = esp_timer_get_time();

    if (cal.on) calAfterIo();
    else {
      // FxLMS update with the error-mic block (reference filtered through the measured path)
      for (int k = 0; k < 2; k++) {
        if (!act[k]) continue;
        float Sr = S[k][0], Si = S[k][1], g0 = 0, g1 = 0, pw = Sr * Sr + Si * Si + 1e-9f;
        for (int i = 0; i < N; i++) {
          float fc = Sr * cs[k][i] - Si * sn[k][i], fs = Sr * sn[k][i] + Si * cs[k][i];
          g0 += errm[i] * fc; g1 += errm[i] * fs;
        }
        w0[k] -= mu * g0 / N / pw * 2; w1[k] -= mu * g1 / N / pw * 2;
        float wa = sqrtf(w0[k] * w0[k] + w1[k] * w1[k]);
        if (wa > vmax) { w0[k] *= vmax / wa; w1[k] *= vmax / wa; }    // no wind-up past the speaker limit
      }
      for (int k = 0; k < 2; k++)
        for (int i = 0; i < N; i++) {
          eRe[k] += errm[i] * cs[k][i]; eIm[k] -= errm[i] * sn[k][i];
          rRe[k] += refm[i] * cs[k][i]; rIm[k] -= refm[i] * sn[k][i];
        }
      aN += N;
    }

    // ring buffers for features / spectra / raw recording (core 0 reads them)
    uint32_t c0 = audioCount;
    for (int i = 0; i < N; i++) { refRing[(c0 + i) & (REF_RING - 1)] = refm[i]; errRing[(c0 + i) & (ERR_RING - 1)] = errm[i]; }
    blockUs[(c0 / N) & (BLK_RING - 1)] = tRead;
    audioCount = c0 + N;
    blocks++;

    // 0.5 s window: levels, reduction (error/reference ratio vs ANC-off baseline), guard
    if (aN >= FS / 2) {
      float outAmp = 0; bool lim = false;
      for (int k = 0; k < 2; k++) {
        float ea = 2 * sqrt(eRe[k] * eRe[k] + eIm[k] * eIm[k]) / aN, ra = 2 * sqrt(rRe[k] * rRe[k] + rIm[k] * rIm[k]) / aN;
        errDb[k] = 20 * log10f(ea + 1e-9f);
        float ratio = errDb[k] - 20 * log10f(ra + 1e-9f);   // robust to the motor getting louder/quieter
        if (!act[k] || isnan(baseRatio[k])) { if (!act[k] && !stopped) baseRatio[k] = ratio; red[k] = 0; }
        else red[k] = baseRatio[k] - ratio;
        if (act[k] && red[k] < -3) {
          if (++bad[k] >= 4) { w0[k] = w1[k] = 0; bad[k] = 0; portENTER_CRITICAL(&mux); st.guardResets++; setMsg("louder than baseline -> weights reset (re-calibrate if this repeats)"); portEXIT_CRITICAL(&mux); }
        } else bad[k] = 0;
        float wa = sqrtf(w0[k] * w0[k] + w1[k] * w1[k]);
        outAmp += wa; if (act[k] && wa >= vmax * 0.99f) lim = true;
        eRe[k] = eIm[k] = rRe[k] = rIm[k] = 0;
      }
      aN = 0;
      portENTER_CRITICAL(&mux);
      st.enabled = ancOn; st.stopped = stopped; st.atLimit = lim; st.out = outAmp;
      for (int k = 0; k < 2; k++) { st.active[k] = act[k]; st.outOfRange[k] = oor[k]; st.f[k] = f[k]; st.red[k] = act[k] ? red[k] : 0; st.errDb[k] = errDb[k]; }
      st.mu = mu; st.vmax = vmax; st.order = order; st.shaftHz = shaftS; st.blocks = blocks;
      st.loadPct = winUs ? 100.0f * busyUs / winUs : 0; busyUs = winUs = 0;
      portEXIT_CRITICAL(&mux);
    }
    if (cal.on) {                         // keep status fresh while calibrating
      portENTER_CRITICAL(&mux); st.enabled = false; st.f[0] = cal.f; portEXIT_CRITICAL(&mux);
    }
    int64_t t1 = esp_timer_get_time();
    busyUs += (uint32_t)((tIo - t0) + (t1 - tRead));     // compute time, excluding the I2S wait
    winUs += 4000;
  }
}

// ======================================================================= core 0: sensors
// ---------------- ADXL345 (registers, I2C 400 kHz, FIFO stream mode, 1600 Hz, +-4 g full-res) ----------------
const uint8_t ADXL = 0x53;
const int VIB_FS = 1600, VIB_RING = 2048;
int16_t vx[VIB_RING], vy[VIB_RING], vz[VIB_RING];
uint32_t vibCount = 0; int64_t vibLastUs = 0;
bool adxlOk = false;
const float ADXL_G_PER_LSB = 0.0039f;          // full resolution: 3.9 mg/LSB at any range

void adxlW(uint8_t r, uint8_t v) { Wire.beginTransmission(ADXL); Wire.write(r); Wire.write(v); Wire.endTransmission(); }
int adxlR(uint8_t r) { Wire.beginTransmission(ADXL); Wire.write(r); if (Wire.endTransmission(false)) return -1; if (Wire.requestFrom(ADXL, (uint8_t)1) != 1) return -1; return Wire.read(); }

bool adxlBegin() {
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  if (adxlR(0x00) != 0xE5) return false;     // DEVID
  adxlW(0x2D, 0x00);                         // standby
  adxlW(0x2C, 0x0E);                         // BW_RATE: 1600 Hz
  adxlW(0x31, 0x09);                         // DATA_FORMAT: FULL_RES, +-4 g
  adxlW(0x38, 0x80 | 16);                    // FIFO_CTL: stream mode, watermark 16
  adxlW(0x2D, 0x08);                         // measure
  return true;
}

void adxlPoll() {                             // call every few ms: FIFO holds 32 samples (20 ms)
  if (!adxlOk) return;
  int n = adxlR(0x39); if (n < 0) return; n &= 0x3F;
  for (int i = 0; i < n; i++) {
    Wire.beginTransmission(ADXL); Wire.write(0x32); if (Wire.endTransmission(false)) return;
    if (Wire.requestFrom(ADXL, (uint8_t)6) != 6) return;
    uint8_t b[6]; for (int j = 0; j < 6; j++) b[j] = Wire.read();
    uint32_t c = vibCount & (VIB_RING - 1);
    vx[c] = (int16_t)(b[0] | b[1] << 8); vy[c] = (int16_t)(b[2] | b[3] << 8); vz[c] = (int16_t)(b[4] | b[5] << 8);
    vibCount++;
  }
  if (n) vibLastUs = esp_timer_get_time();
}

// ---------------- ACS712 ----------------
float iZeroMv = 0; double iSum = 0, iSum2 = 0; uint32_t iN = 0; float iMean = NAN, iRipple = NAN;
void currentBegin() {
  analogReadResolution(12); analogSetPinAttenuation(PIN_CURRENT, ADC_11db);
  double s = 0; for (int i = 0; i < 400; i++) { s += analogReadMilliVolts(PIN_CURRENT); delayMicroseconds(250); }
  iZeroMv = s / 400;                          // auto-zero: motor must be off at boot
}
void currentSample() {
  for (int i = 0; i < 4; i++) {
    float a = (analogReadMilliVolts(PIN_CURRENT) - iZeroMv) / (ACS_MV_PER_A * ACS_DIVIDER);
    iSum += a; iSum2 += a * a; iN++;
  }
}
void currentWindow() {                       // every 0.5 s: mean and AC ripple (std)
  if (!iN) return;
  iMean = iSum / iN; iRipple = sqrt(fmax(0.0, iSum2 / iN - (double)iMean * iMean));
  iSum = iSum2 = 0; iN = 0;
}

// ---------------- DS18B20 (1 Hz, non-blocking) ----------------
#if HAVE_DS18B20
OneWire ow(PIN_DS18B20); DallasTemperature ds(&ow);
#endif
bool dsOk = false; float tempC = NAN; uint32_t dsReqMs = 0;
void tempBegin() {
#if HAVE_DS18B20
  ds.begin(); dsOk = ds.getDeviceCount() > 0; ds.setWaitForConversion(false); ds.setResolution(12);
  if (dsOk) { ds.requestTemperatures(); dsReqMs = millis(); }
#endif
}
void tempPoll() {
#if HAVE_DS18B20
  if (!dsOk || millis() - dsReqMs < 1000) return;
  float t = ds.getTempCByIndex(0);
  tempC = (t > -55 && t < 125 && t != 85.0f) ? t : NAN;   // 85 = power-on value, -127 = disconnected
  ds.requestTemperatures(); dsReqMs = millis();
#endif
}

// ---------------- motor PWM, buzzer, RGB ----------------
// Channels are spread over separate LEDC timers (timer = channel/2) and given distinct start
// frequencies so spread-spectrum hopping on the motor never moves the LED/buzzer timers.
const int CH_MOTOR = 0, CH_R = 2, CH_G = 3, CH_B = 4, CH_BUZ = 6;
enum PwmMode { PWM_FIXED, PWM_SPREAD };
PwmMode pwmMode = PWM_FIXED; uint32_t pwmHz = PWM_BASE_HZ; int motorDuty = 0;

void outputsBegin() {
  ledcAttachChannel(PIN_MOTOR, PWM_BASE_HZ, PWM_BITS, CH_MOTOR); ledcWrite(PIN_MOTOR, 0);
  ledcAttachChannel(PIN_R, 5000, 8, CH_R); ledcAttachChannel(PIN_G, 5000, 8, CH_G); ledcAttachChannel(PIN_B, 5000, 8, CH_B);
  ledcAttachChannel(PIN_BUZZER, 2000, 10, CH_BUZ); ledcWrite(PIN_BUZZER, 0);
}
void setMotor(int duty) { motorDuty = constrain(duty, 0, 100); ledcWrite(PIN_MOTOR, (uint32_t)motorDuty * ((1 << PWM_BITS) - 1) / 100); }
void pwmHop() {                              // spread spectrum: smear the PWM tone over +-15 %
  if (pwmMode != PWM_SPREAD) return;
  uint32_t f = PWM_BASE_HZ * (1.0f + PWM_SPREAD_FRAC * ((esp_random() & 0xFFFF) / 32767.5f - 1.0f));
  ledcChangeFrequency(PIN_MOTOR, f, PWM_BITS); pwmHz = f;
  setMotor(motorDuty);
}
void rgb(int r, int g, int b) {
  if (RGB_COMMON_ANODE) { r = 255 - r; g = 255 - g; b = 255 - b; }
  ledcWrite(PIN_R, r); ledcWrite(PIN_G, g); ledcWrite(PIN_B, b);
}
uint32_t beepUntil = 0;
void beep(uint32_t hz, uint32_t ms) { ledcWriteTone(PIN_BUZZER, hz); beepUntil = millis() + ms; }
void beepPoll() { if (beepUntil && (int32_t)(millis() - beepUntil) >= 0) { ledcWriteTone(PIN_BUZZER, 0); beepUntil = 0; } }

// ======================================================================= DSP (core 0)
void service();                                // drains the ADXL345 FIFO + samples current; called inside long DSP loops
const int FFT_N = 1024, VFFT_N = 2048, NB = FFT_N / 2 + 1;
float twc[VFFT_N / 2], tws[VFFT_N / 2];
float hann[FFT_N];
float *work, *fre, *fim, *pw;                 // heap, allocated once at boot

void fftInit() { for (int i = 0; i < VFFT_N / 2; i++) { twc[i] = cosf(2 * PI * i / VFFT_N); tws[i] = -sinf(2 * PI * i / VFFT_N); }
                 for (int i = 0; i < FFT_N; i++) hann[i] = 0.5f - 0.5f * cosf(2 * PI * i / (FFT_N - 1)); }
void fft(float* re, float* im, int n) {       // in-place radix-2, n <= VFFT_N
  for (int i = 1, j = 0; i < n; i++) { int b = n >> 1; for (; j & b; b >>= 1) j ^= b; j ^= b; if (i < j) { float t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; } }
  for (int len = 2; len <= n; len <<= 1) {
    int step = VFFT_N / len;
    for (int i = 0; i < n; i += len)
      for (int j = 0; j < len / 2; j++) {
        float wr = twc[j * step], wi = tws[j * step];
        int a = i + j, b = a + len / 2;
        float xr = re[b] * wr - im[b] * wi, xi = re[b] * wi + im[b] * wr;
        re[b] = re[a] - xr; im[b] = im[a] - xi; re[a] += xr; im[a] += xi;
      }
  }
}

// Butterworth biquads (RBJ / bilinear with prewarp = scipy.signal.butter), order 4 = 2 sections
struct Biq { float b0, b1, b2, a1, a2; };
void butter4(Biq* s, float fc, float fs, bool high) {
  const float Q[2] = {0.54119610f, 1.30656296f};
  float w = 2 * PI * fc / fs, c = cosf(w), sw = sinf(w);
  for (int k = 0; k < 2; k++) {
    float al = sw / (2 * Q[k]), a0 = 1 + al;
    if (high) { s[k].b0 = (1 + c) / 2 / a0; s[k].b1 = -(1 + c) / a0; }
    else      { s[k].b0 = (1 - c) / 2 / a0; s[k].b1 = (1 - c) / a0; }
    s[k].b2 = s[k].b0; s[k].a1 = -2 * c / a0; s[k].a2 = (1 - al) / a0;
  }
}
void biqRun(const Biq& q, float* x, int n, bool rev) {
  float z1 = 0, z2 = 0;
  for (int t = 0; t < n; t++) { int i = rev ? n - 1 - t : t; float y = q.b0 * x[i] + z1; z1 = q.b1 * x[i] - q.a1 * y + z2; z2 = q.b2 * x[i] - q.a2 * y; x[i] = y; }
}
// zero-phase filtering (forward + backward) with odd-extension padding, like scipy sosfiltfilt
void filtfilt(Biq* secs, int ns, float* x, int n, int pad) {
  // x has 'pad' free slots before and after: x[-pad..n+pad)
  float a = x[0], b = x[n - 1];
  for (int i = 1; i <= pad; i++) { x[-i] = 2 * a - x[i < n ? i : n - 1]; x[n - 1 + i] = 2 * b - x[n - 1 - i > 0 ? n - 1 - i : 0]; }
  for (int k = 0; k < ns; k++) { biqRun(secs[k], x - pad, n + 2 * pad, false); service(); }
  for (int k = 0; k < ns; k++) { biqRun(secs[k], x - pad, n + 2 * pad, true); service(); }
}
Biq aHP[2], aLP[2], vHP[2], vLP[2];
const int PAD = 512;

// copy the last n samples from a ring into dst
void ringCopy(const float* ring, int size, uint32_t end, int n, float* dst) { for (int i = 0; i < n; i++) dst[i] = ring[(end - n + i) & (size - 1)]; }

// prefilter (HP 50 + LP 7000, zero phase) then Welch power spectrum (1024, hop 512, Hann)
// = bridge/common.py prefilter_audio + audio_power_spectrum. Returns rms of the prefiltered audio.
float audioSpectrum(const float* ring, int size, int n, float* power) {
  float* x = work + PAD;
  ringCopy(ring, size, audioCount, n, x);
  Biq secs[4] = {aHP[0], aHP[1], aLP[0], aLP[1]};
  filtfilt(secs, 4, x, n, PAD);
  double ss = 0; for (int i = 0; i < n; i++) ss += x[i] * x[i];
  float wsum = 0; for (int i = 0; i < FFT_N; i++) wsum += hann[i];
  for (int i = 0; i < NB; i++) power[i] = 0;
  int frames = 0;
  for (int s = 0; s + FFT_N <= n; s += FFT_N / 2, frames++) {
    float m = 0; for (int i = 0; i < FFT_N; i++) m += x[s + i]; m /= FFT_N;
    for (int i = 0; i < FFT_N; i++) { fre[i] = (x[s + i] - m) * hann[i]; fim[i] = 0; }
    fft(fre, fim, FFT_N);
    service();
    for (int i = 0; i < NB; i++) { float a = 2 * sqrtf(fre[i] * fre[i] + fim[i] * fim[i]) / wsum; power[i] += a * a; }
  }
  if (frames) for (int i = 0; i < NB; i++) power[i] /= frames;
  return sqrt(ss / n);
}

void logBands(const float* power, float df, int nbins, int nb, float lo, float hi, float* out) {
  for (int b = 0; b < nb; b++) {
    float e0 = lo * powf(hi / lo, (float)b / nb), e1 = lo * powf(hi / lo, (float)(b + 1) / nb);
    double s = 0; int c = 0;
    for (int i = 0; i < nbins; i++) { float f = i * df; if (f >= e0 && f < e1) { s += power[i]; c++; } }
    if (!c) { int i = (int)lroundf(e0 / df); if (i >= nbins) i = nbins - 1; s = power[i]; c = 1; }
    out[b] = 10 * log10f(s / c + 1e-14f);
  }
}
float whineHz(const float* p, float df) {     // peak 80-2000 Hz, parabolic refinement (log power)
  int i0 = (int)ceilf(80 / df), i1 = (int)floorf(2000 / df), bi = i0;
  for (int i = i0; i <= i1; i++) if (p[i] > p[bi]) bi = i;
  float a = logf(p[bi - 1] + 1e-20f), b = logf(p[bi] + 1e-20f), c = logf(p[bi + 1] + 1e-20f), den = a - 2 * b + c;
  float d = den != 0 ? 0.5f * (a - c) / den : 0;
  return (bi + clampf(d, -0.5f, 0.5f)) * df;
}
void displayBins(const float* p, float* bins) {    // 128 bins 0-4 kHz, dB + 100 (common.display_spectrum)
  for (int b = 0; b < 128; b++) bins[b] = 10 * log10f((p[2 * b] + p[2 * b + 1]) / 2 + 1e-14f) + 100;
}

// vibration: last 1 s (1600 samples) -> prefilter (mean, HP 3 Hz + LP 720 Hz zero phase) ->
// Hann, zero-pad to 2048, power summed over axes -> 16 log bands 2 Hz..0.9 Nyquist,
// amplitude at 1x / 2x Hall shaft speed, RMS  (= common.vib_features with exact shaft speed)
struct VibFeat { float bands[16], v1, v2, rms, sx, sy, sz; bool ok; };
VibFeat vibFeatures(float shaftHz) {
  VibFeat r = {}; const int n = VIB_FS;
  if (!adxlOk || vibCount < (uint32_t)n) { for (int i = 0; i < 16; i++) r.bands[i] = -120; return r; }
  uint32_t end = vibCount;
  float* x = work + PAD;
  static float pwv[VFFT_N / 2 + 1]; for (int i = 0; i <= VFFT_N / 2; i++) pwv[i] = 0;
  float wsum = 0; for (int i = 0; i < n; i++) wsum += 0.5f - 0.5f * cosf(2 * PI * i / (n - 1));
  double ss = 0; float sd[3];
  for (int a = 0; a < 3; a++) {
    int16_t* src = a == 0 ? vx : a == 1 ? vy : vz;
    double m = 0; for (int i = 0; i < n; i++) { x[i] = src[(end - n + i) & (VIB_RING - 1)] * ADXL_G_PER_LSB; m += x[i]; } m /= n;
    for (int i = 0; i < n; i++) x[i] -= m;
    Biq secs[4] = {vHP[0], vHP[1], vLP[0], vLP[1]};
    filtfilt(secs, 4, x, n, PAD);
    double s2 = 0; for (int i = 0; i < n; i++) s2 += x[i] * x[i];
    ss += s2; sd[a] = sqrt(s2 / n);
    for (int i = 0; i < VFFT_N; i++) { fre[i] = i < n ? x[i] * (0.5f - 0.5f * cosf(2 * PI * i / (n - 1))) : 0; fim[i] = 0; }
    fft(fre, fim, VFFT_N);
    service();
    for (int i = 0; i <= VFFT_N / 2; i++) { float m2 = 2 * sqrtf(fre[i] * fre[i] + fim[i] * fim[i]) / wsum; pwv[i] += m2 * m2; }
  }
  float df = (float)VIB_FS / VFFT_N;
  logBands(pwv, df, VFFT_N / 2 + 1, 16, 2.0f, 0.9f * VIB_FS / 2, r.bands);
  auto ampAt = [&](float f) -> float {
    if (f <= 0 || f >= 0.45f * VIB_FS) return 0;
    int i = (int)lroundf(f / df); float m = 0;
    for (int j = max(i - 1, 0); j <= min(i + 1, VFFT_N / 2); j++) m = fmaxf(m, pwv[j]);
    return sqrtf(m);
  };
  r.v1 = ampAt(shaftHz); r.v2 = ampAt(2 * shaftHz);
  r.rms = sqrt(ss / n); r.sx = sd[0]; r.sy = sd[1]; r.sz = sd[2]; r.ok = true;
  return r;
}

// ======================================================================= JSON out (core 0 only)
char ob[9000]; int ol = 0;
void jb(const char* s) { while (*s && ol < (int)sizeof(ob) - 1) ob[ol++] = *s++; ob[ol] = 0; }
void jf(const char* k, float v, int d = 2) {
  char t[48];
  if (isnan(v) || isinf(v)) snprintf(t, sizeof(t), ",\"%s\":null", k); else snprintf(t, sizeof(t), ",\"%s\":%.*f", k, d, v);
  jb(t);
}
void jbool(const char* k, bool v) { char t[40]; snprintf(t, sizeof(t), ",\"%s\":%s", k, v ? "true" : "false"); jb(t); }
void jstr(const char* k, const char* v) { char t[120]; snprintf(t, sizeof(t), ",\"%s\":\"%s\"", k, v); jb(t); }
void jarr(const char* k, const float* v, int n, int d) {
  char t[24]; snprintf(t, sizeof(t), ",\"%s\":[", k); jb(t);
  for (int i = 0; i < n; i++) { if (isnan(v[i])) snprintf(t, sizeof(t), "%snull", i ? "," : ""); else snprintf(t, sizeof(t), "%s%.*f", i ? "," : "", d, v[i]); jb(t); }
  jb("]");
}
void jstart(const char* type) { ol = 0; char t[80]; snprintf(t, sizeof(t), "{\"t\":\"%s\",\"ts\":%lu,\"vid\":\"%s\"", type, (unsigned long)millis(), VID); jb(t); }
void jsend() { jb("}\n"); Serial.write((const uint8_t*)ob, ol); }

void sendAck(bool ok, const char* reason) { jstart("ack"); jbool("ok", ok); jstr("reason", reason); jsend(); }
void sendLog(const char* msg) { jstart("log"); jstr("msg", msg); jsend(); }

// base64 (raw recording stream)
const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
void jb64(const uint8_t* d, int n) {
  char t[5]; t[4] = 0;
  for (int i = 0; i < n; i += 3) {
    uint32_t v = d[i] << 16 | (i + 1 < n ? d[i + 1] << 8 : 0) | (i + 2 < n ? d[i + 2] : 0);
    t[0] = B64[v >> 18 & 63]; t[1] = B64[v >> 12 & 63]; t[2] = i + 1 < n ? B64[v >> 6 & 63] : '='; t[3] = i + 2 < n ? B64[v & 63] : '=';
    jb(t);
  }
}

// ======================================================================= JSON in (tiny flat parser)
const char* jfind(const char* s, const char* key) {
  char k[40]; snprintf(k, sizeof(k), "\"%s\"", key);
  const char* p = strstr(s, k); if (!p) return nullptr;
  p += strlen(k); while (*p == ' ' || *p == ':') p++;
  return p;
}
bool jgetNum(const char* s, const char* key, float& v) { const char* p = jfind(s, key); if (!p || !(isdigit(*p) || *p == '-' || *p == '.')) return false; v = atof(p); return true; }
bool jgetBool(const char* s, const char* key, bool& v) { const char* p = jfind(s, key); if (!p) return false; if (!strncmp(p, "true", 4)) { v = true; return true; } if (!strncmp(p, "false", 5)) { v = false; return true; } return false; }
bool jgetStr(const char* s, const char* key, char* out, int n) {
  const char* p = jfind(s, key); if (!p || *p != '"') return false; p++;
  int i = 0; while (*p && *p != '"' && i < n - 1) out[i++] = *p++; out[i] = 0; return true;
}
int jgetArr(const char* s, const char* key, float* out, int n) {
  const char* p = jfind(s, key); if (!p || *p != '[') return -1; p++;
  int c = 0;
  while (*p && *p != ']' && c < n) { while (*p == ' ' || *p == ',') p++; if (*p == ']') break; out[c++] = atof(p); while (*p && *p != ',' && *p != ']') p++; }
  return c;
}

// ======================================================================= core 0 state
bool rawOn = false; uint32_t rawA = 0, rawV = 0, rawSeq = 0;
char tier[16] = ""; char lastFault[16] = ""; uint32_t lastInfMs = 0;
float lastWhine = NAN, lastCentroid = NAN, lastOrderMeas = NAN, lastSplRef = NAN, lastSplErr = NAN;
VibFeat lastVib = {};

void post(CmdType t, float a) { Cmd c = {t, a}; xQueueSend(cmdQ, &c, 0); }

void handleLine(char* s) {
  char cmd[24] = "", typ[8] = "";
  if (jgetStr(s, "t", typ, sizeof(typ)) && !strcmp(typ, "inf")) {       // diagnosis from the backend
    lastInfMs = millis();
    jgetStr(s, "tier", tier, sizeof(tier));
    char f[16] = ""; jgetStr(s, "fault", f, sizeof(f));
    if (strcmp(f, lastFault)) { if (!strcmp(tier, "SEVERE")) beep(2800, 400); strncpy(lastFault, f, sizeof(lastFault) - 1); }
    float p[MAX_PROT]; int n = jgetArr(s, "protected_hz", p, MAX_PROT);
    float sh = hallShaftHz();
    if (n >= 0 && sh > 1) {                    // store as shaft orders so they follow the speed
      int m = 0;
      for (int i = 0; i < n; i++) if (p[i] > 0) protOrders[m++] = p[i] / sh;
      protCount = m;
    } else if (n == 0) protCount = 0;
    return;
  }
  if (!jgetStr(s, "cmd", cmd, sizeof(cmd))) { sendAck(false, "malformed json"); return; }
  if (!strcmp(cmd, "set")) {
    float v; bool b; char m[16];
    if (jgetStr(s, "pwm_mode", m, sizeof(m))) {
      pwmMode = !strcmp(m, "spread") ? PWM_SPREAD : PWM_FIXED;        // "optimize" is not implemented -> fixed
      if (pwmMode == PWM_FIXED) { ledcChangeFrequency(PIN_MOTOR, PWM_BASE_HZ, PWM_BITS); pwmHz = PWM_BASE_HZ; setMotor(motorDuty); }
    }
    if (jgetNum(s, "motor_duty", v)) setMotor((int)v);
    if (jgetNum(s, "whine_order", v)) {
      if (v < 2) { sendAck(false, "whine_order must be >= 2 (order 1 is the protected shaft tone)"); return; }
      post(C_ORDER, v);
    }
    if (jgetNum(s, "anc_mu", v)) post(C_MU, v);
    if (jgetNum(s, "anc_vmax", v)) post(C_VMAX, v);
    if (jgetBool(s, "anc", b)) {
      if (b && !path[0].ok) { sendAck(false, "calibrate first (anc_calibrate, motor stopped)"); return; }
      post(C_ANC, b ? 1 : 0);
    }
    if (jgetBool(s, "raw", b)) { rawOn = b; rawA = audioCount; rawV = vibCount; rawSeq = 0; }
    sendAck(true, "command applied");
  } else if (!strcmp(cmd, "anc_calibrate")) {
    float f = 0; jgetNum(s, "f_hz", f);
    if (hallShaftHz() * 60 >= STOPPED_RPM) { sendAck(false, "stop the motor before calibrating"); return; }
    post(C_CAL, f); beep(1500, 120);
    sendAck(true, "calibration started (~9 s, keep quiet)");
  } else if (!strcmp(cmd, "anc_abort")) { post(C_CAL_ABORT, 0); sendAck(true, "aborted"); }
  else if (!strcmp(cmd, "anc_reset")) { post(C_RESET, 0); sendAck(true, "weights reset"); }
  else if (!strcmp(cmd, "ping")) sendAck(true, "pong");
  else sendAck(false, "unknown cmd");
}

void readSerial() {
  static char line[1024]; static int n = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') { if (n) { line[n] = 0; handleLine(line); n = 0; } }
    else if (n < (int)sizeof(line) - 1) line[n++] = c;
    else n = 0;                                                     // overlong line: drop
  }
}

AncStatus snap() { AncStatus s; portENTER_CRITICAL(&mux); s = st; portEXIT_CRITICAL(&mux); return s; }
const char* calName(uint8_t c) { return c == CAL_RUNNING ? "running" : c == CAL_OK ? "ok" : c == CAL_FAILED ? "failed" : "none"; }

float quickDb(const float* ring, int size, int n) {    // tel level: last n samples, DC removed, dBFS + 100
  uint32_t end = audioCount; double m = 0, s = 0;
  for (int i = 0; i < n; i++) m += ring[(end - n + i) & (size - 1)];
  m /= n;
  for (int i = 0; i < n; i++) { double v = ring[(end - n + i) & (size - 1)] - m; s += v * v; }
  return 20 * log10(sqrt(s / n) + 1e-9) + 100;
}

void sendTel() {
  AncStatus a = snap();
  lastSplRef = quickDb(refRing, REF_RING, 1600); lastSplErr = quickDb(errRing, ERR_RING, 1600);
  float sh = hallShaftHz();
  jstart("tel"); jstr("src", "esp32s3"); jstr("rpm_src", "hall");
  jf("rpm", sh * 60, 1); jf("current_a", iMean, 3); jf("temp_c", tempC, 2);
  jf("vib_rms_g", lastVib.ok ? lastVib.rms : NAN, 4);
  float xyz[3] = {lastVib.ok ? lastVib.sx : NAN, lastVib.ok ? lastVib.sy : NAN, lastVib.ok ? lastVib.sz : NAN};
  jarr("vib_xyz", xyz, 3, 4);
  jf("spl_ref_db", lastSplRef, 1); jf("spl_err_db", lastSplErr, 1);
  char t[48]; snprintf(t, sizeof(t), ",\"pwm_hz\":%lu", (unsigned long)pwmHz); jb(t);
  jstr("pwm_mode", pwmMode == PWM_SPREAD ? "spread" : "fixed"); jf("motor_duty", motorDuty, 0);
  jbool("stopped", a.stopped);
  jbool("anc_on", a.enabled); jbool("anc_active", a.active[0] || a.active[1]);
  jf("anc_freq", a.f[0], 1); jf("anc_freq2", a.f[1], 1);
  jf("anc_reduction_db", (a.active[0] ? a.red[0] : NAN), 1); jf("anc_reduction2_db", (a.active[1] ? a.red[1] : NAN), 1);
  jf("anc_err_db", a.errDb[0], 1);
  jf("anc_out", a.out, 3); jbool("anc_at_limit", a.atLimit); jbool("anc_out_of_range", a.outOfRange[0] || a.outOfRange[1]);
  jf("anc_mu", a.mu, 4); jf("anc_vmax", a.vmax, 2);
  jstr("anc_cal", calName(a.cal)); jf("anc_cal_pct", a.calPct, 0); jf("anc_tau_ms", a.cal == CAL_OK || path[0].ok ? tau * 1000 : NAN, 2);
  jf("anc_cal_center_hz", calCenter, 1);
  jstr("anc_msg", a.msg);
  // protected tones: 1x shaft always (+ the backend's, as live Hz)
  float pr[MAX_PROT + 1]; int np = 0;
  if (sh > 1) { pr[np++] = sh; for (int i = 0; i < protCount && i < MAX_PROT; i++) pr[np++] = protOrders[i] * sh; }
  jarr("protected_hz", pr, np, 1);
  jf("whine_order", a.order, 2); jf("order_meas", lastOrderMeas, 2);
  jf("audio_load_pct", a.loadPct, 1);
  jbool("raw", rawOn); jbool("vib_cancel_on", false); jf("vib_cancel_effort", 0, 0);
  jsend();
}

float specP[NB];
void sendSpec(bool err) {
  AncStatus a = snap();
  audioSpectrum(err ? errRing : refRing, err ? ERR_RING : REF_RING, 4000, specP);   // last 0.25 s
  float bins[128]; displayBins(specP, bins);
  jstart("spec"); jstr("mic", err ? "err" : "ref"); jf("fmax", 4000, 0); jarr("bins", bins, 128, 1);
  if (err) {
    jf("reduction_db", a.active[0] ? a.red[0] : NAN, 1); jf("whine_hz", a.f[0], 1);
    float hz[2]; int n = 0; for (int k = 0; k < 2; k++) if (a.active[k]) hz[n++] = a.f[k];
    jarr("anc_hz", hz, n, 1);
  }
  jsend();
}

float featP[NB];
void sendFeat() {
  float sh = hallShaftHz();
  float rms = audioSpectrum(refRing, REF_RING, FS, featP);                // 1 s window, same as WIN_S
  float df = (float)FS / FFT_N;
  float ab[32]; logBands(featP, df, NB, 32, 50, 8000, ab);
  float wh = whineHz(featP, df);
  double sp = 0, sfp = 0; for (int i = 0; i < NB; i++) { float f = i * df; if (f >= 50 && f <= 8000) { sp += featP[i]; sfp += f * featP[i]; } }
  lastCentroid = sp > 0 ? sfp / sp : NAN;
  const char* src = "hall";
  float shaft = sh;
  if (shaft <= 1) { AncStatus a = snap(); shaft = wh / a.order; src = "whine"; }     // Hall stopped: fall back
  lastVib = vibFeatures(shaft);
  lastWhine = wh; lastOrderMeas = sh > 1 ? wh / sh : NAN;
  currentWindow();
  jstart("feat"); jf("rpm", sh * 60, 1); jstr("rpm_src", src); jf("shaft_hz", shaft, 3);
  jarr("audio_bands", ab, 32, 2); jarr("vib_bands", lastVib.bands, 16, 2);
  jf("vib_1x", lastVib.v1, 5); jf("vib_2x", lastVib.v2, 5); jf("vib_rms", lastVib.ok ? lastVib.rms : NAN, 5);
  jf("audio_rms_db", 20 * log10f(rms + 1e-9f), 2); jf("whine_hz", wh, 2); jf("centroid_hz", lastCentroid, 1);
  jf("i_mean", iMean, 3); jf("i_ripple", iRipple, 3); jf("temp_c", tempC, 2);
  jbool("vib_ok", lastVib.ok); jf("feat_v", 1, 0);
  jsend();
}

// raw recording stream: ref mic int16 @16 kHz and ADXL345 int16 xyz @1600 Hz, base64, every 100 ms
void sendRaw() {
  static int16_t a16[2048]; static int16_t v16[3 * 256];
  uint32_t end = audioCount, gap = 0;
  if (end - rawA > (uint32_t)REF_RING - 2048) { gap += end - rawA - 1600; rawA = end - 1600; }
  int n = min((int)(end - rawA), 2048);
  if (n > 0) {
    for (int i = 0; i < n; i++) a16[i] = (int16_t)clampf(refRing[(rawA + i) & (REF_RING - 1)] * 32767.0f, -32768, 32767);
    int64_t t0 = blockUs[(rawA / N) & (BLK_RING - 1)] - (int64_t)(N - rawA % N) * 1000000 / FS;   // block read time - age
    jstart("raw"); char t[96]; snprintf(t, sizeof(t), ",\"seq\":%lu,\"fs\":%d,\"i0\":%lu,\"t0_us\":%lld,\"gap\":%lu,\"a\":\"", (unsigned long)rawSeq, FS, (unsigned long)rawA, (long long)t0, (unsigned long)gap); jb(t);
    jb64((uint8_t*)a16, n * 2); jb("\""); jsend();
    rawA += n;
  }
  uint32_t ve = vibCount, vgap = 0;
  if (ve - rawV > VIB_RING - 256) { vgap = ve - rawV - 160; rawV = ve - 160; }
  int m = min((int)(ve - rawV), 256);
  if (m > 0) {
    for (int i = 0; i < m; i++) { uint32_t c = (rawV + i) & (VIB_RING - 1); v16[3 * i] = vx[c]; v16[3 * i + 1] = vy[c]; v16[3 * i + 2] = vz[c]; }
    int64_t t1 = vibLastUs - (int64_t)(ve - (rawV + m)) * 1000000 / VIB_FS;              // time of the last copied sample
    jstart("rawv"); char t[128]; snprintf(t, sizeof(t), ",\"seq\":%lu,\"fs\":%d,\"i0\":%lu,\"t_last_us\":%lld,\"gap\":%lu,\"g_per_lsb\":%.4f,\"v\":\"", (unsigned long)rawSeq, VIB_FS, (unsigned long)rawV, (long long)t1, (unsigned long)vgap, ADXL_G_PER_LSB); jb(t);
    jb64((uint8_t*)v16, m * 6); jb("\""); jsend();
    rawV += m;
  }
  rawSeq++;
}

void ledUpdate() {
  AncStatus a = snap(); uint32_t ms = millis();
  if (a.cal == CAL_RUNNING) { bool on = (ms / 200) % 2; rgb(on ? 120 : 0, 0, on ? 120 : 0); return; }   // magenta blink
  if (a.stopped) { rgb(0, 0, 60); return; }                                                         // blue
  if (!lastInfMs || ms - lastInfMs > 5000) { bool on = (ms / 500) % 2; rgb(on ? 20 : 0, on ? 20 : 0, on ? 20 : 0); return; }   // no backend
  if (!strcmp(tier, "SEVERE")) rgb(160, 0, 0);
  else if (!strcmp(tier, "MODERATE")) rgb(160, 50, 0);
  else if (!strcmp(tier, "MILD")) rgb(120, 100, 0);
  else rgb(0, 120, 0);
}

// ======================================================================= setup / tasks
void service() { adxlPoll(); currentSample(); }

void sensorTask(void*) {                     // core 0: sensors, features, comms
  currentBegin();                           // motor is off here: auto-zero
  adxlOk = adxlBegin();
  tempBegin();
  pinMode(PIN_HALL, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_HALL), hallIsr, FALLING);   // ISR lives on core 0
  char m[170];
  snprintf(m, sizeof(m), "QuietDrive S3 boot: adxl345 %s, ds18b20 %s, current zero %.0f mV, whine_order %.2f, path %s",
           adxlOk ? "ok" : "MISSING", dsOk ? "ok" : (HAVE_DS18B20 ? "MISSING" : "lib not installed"), iZeroMv, order, calName(st.cal));
  sendLog(m);
  beep(2000, 80);

  uint32_t tTel = 0, tSpec = 0, tFeat = 0, tRaw = 0, tHop = 0, tLed = 0;
  char lastMsg[64] = ""; uint8_t lastCal = st.cal; float lastOrder = order;
  for (;;) {
    uint32_t ms = millis();
    readSerial();
    service();
    tempPoll();
    beepPoll();
    if (ms - tHop >= 2) { tHop = ms; pwmHop(); }
    if (ms - tFeat >= 500) { tFeat = ms; sendFeat(); }
    if (ms - tTel >= 100) { tTel = ms; sendTel(); }
    if (ms - tSpec >= (rawOn ? 400u : 200u)) { tSpec = ms; sendSpec(false); service(); sendSpec(true); }   // half rate while recording raw
    if (rawOn && ms - tRaw >= 100) { tRaw = ms; sendRaw(); }
    if (ms - tLed >= 100) { tLed = ms; ledUpdate(); }
    AncStatus a = snap();
    if (strcmp(a.msg, lastMsg)) { strncpy(lastMsg, a.msg, sizeof(lastMsg) - 1); sendLog(a.msg); }
    if (a.cal != lastCal) {                  // calibration finished: persist (flash writes stay off core 1)
      if (a.cal == CAL_OK) { savePath(); beep(2400, 150); }
      else if (a.cal == CAL_FAILED) beep(600, 400);
      lastCal = a.cal;
    }
    if (a.order != lastOrder && a.order >= 2) { saveOrder(); lastOrder = a.order; }
    vTaskDelay(1);
  }
}

void setup() {
  Serial.setRxBufferSize(4096);
  Serial.setTxBufferSize(16384);
  Serial.begin(BAUD);
  delay(300);
  outputsBegin(); setMotor(0); rgb(40, 40, 40);

  refRing = (float*)heap_caps_malloc(REF_RING * sizeof(float), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  errRing = (float*)heap_caps_malloc(ERR_RING * sizeof(float), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  work = (float*)malloc((FS + 2 * PAD) * sizeof(float));
  fre = (float*)malloc(VFFT_N * sizeof(float)); fim = (float*)malloc(VFFT_N * sizeof(float));
  if (!refRing || !errRing || !work || !fre || !fim) { sendLog("FATAL: out of memory"); for (;;) delay(1000); }
  memset(refRing, 0, REF_RING * sizeof(float)); memset(errRing, 0, ERR_RING * sizeof(float));

  fftInit();
  butter4(aHP, 50, FS, true); butter4(aLP, 7000, FS, false);
  butter4(vHP, 3, VIB_FS, true); butter4(vLP, 0.45f * VIB_FS, VIB_FS, false);

  loadPath();
  memset(&st, 0, sizeof(st)); st.order = order; st.mu = mu; st.vmax = vmax;
  st.cal = path[0].ok && path[1].ok ? CAL_OK : CAL_NONE; st.tauMs = tau * 1000;
  setMsg(st.cal == CAL_OK ? "path loaded from flash" : "not calibrated: send anc_calibrate (motor stopped)");

  cmdQ = xQueueCreate(16, sizeof(Cmd));
  xTaskCreatePinnedToCore(audioTask, "audio", 8192, nullptr, configMAX_PRIORITIES - 2, nullptr, 1);
  xTaskCreatePinnedToCore(sensorTask, "sensors", 12288, nullptr, 3, nullptr, 0);
}

void loop() { vTaskDelete(nullptr); }       // Arduino's loop task (core 1) is not used
