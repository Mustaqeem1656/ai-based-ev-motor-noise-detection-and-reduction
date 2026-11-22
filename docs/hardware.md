# QuietDrive hardware edition (ESP32-S3)

One **ESP32-S3 DevKitC-1** does the sensing, the fault-aware ANC and the motor drive. It streams
to the FastAPI backend over USB serial (`SOURCE=serial`), which runs the fault models and sends
the diagnosis back to the board and to the dashboard.

- Firmware: [`firmware/quietdrive_s3/quietdrive_s3.ino`](../firmware/quietdrive_s3/quietdrive_s3.ino)
- Self-test: [`firmware/quietdrive_s3_selftest/quietdrive_s3_selftest.ino`](../firmware/quietdrive_s3_selftest/quietdrive_s3_selftest.ino)
- Messages: [`docs/protocol.md`](protocol.md)

> **Status: not yet validated on hardware.** Both sketches pass a full compile check (syntax and
> types, `-Wall`, no warnings) against the installed esp32 Arduino core 3.3.12 for "ESP32S3 Dev
> Module". They have **not** been linked, flashed or run yet, so the build is not fully verified.
> No accuracy or noise-reduction figures for the hardware path exist yet. See
> [What still needs hardware validation](#what-still-needs-hardware-validation).

## Pin table

| Function | ESP32-S3 GPIO | Part / pin | Notes |
|---|---|---|---|
| I2S BCLK | **15** | MAX98357A BCLK, INMP441 #1 SCK, INMP441 #2 SCK | one shared clock |
| I2S WS (LRCLK) | **16** | MAX98357A LRC, INMP441 #1 WS, INMP441 #2 WS | |
| I2S DOUT | **17** | MAX98357A DIN | speaker data |
| I2S DIN | **14** | INMP441 #1 SD **and** #2 SD (one shared line) | the mics take turns by slot |
| Reference mic | — | INMP441 #1, **L/R → GND** (left slot) | near the motor |
| Error mic | — | INMP441 #2, **L/R → 3V3** (right slot) | cabin / driver's ear, near the speaker |
| ADXL345 SDA | **8** | SDA | I2C 400 kHz, address 0x53 (SDO → GND, CS → 3V3) |
| ADXL345 SCL | **9** | SCL | |
| Hall sensor | **5** | A3144 OUT | open collector: 10 kΩ pull-up to **3V3**, falling edge |
| Current | **4** | ACS712-5A OUT via divider | 10 kΩ (top) / 20 kΩ (bottom): 5 V → 3.33 V |
| Temperature | **6** | DS18B20 DQ | 4.7 kΩ pull-up to 3V3 |
| Motor PWM | **7** | logic-level MOSFET gate driver | LEDC 20 kHz, 10-bit |
| Buzzer | **21** | passive buzzer (via transistor if > 20 mA) | |
| RGB status LED | **39 / 40 / 41** | R / G / B (common cathode, ~330 Ω each) | `RGB_COMMON_ANODE` in the sketch |

Constants you may need to change are at the top of `quietdrive_s3.ino`: `MAGNETS` (pulses per
revolution, default 1), `STOPPED_RPM` (300), `DEFAULT_WHINE_ORDER` (21, from the phone calibration
in `bridge/models/calibration.json`), `ACS_DIVIDER`, `PWM_BASE_HZ`, `RGB_COMMON_ANODE`.

## Shared I2S wiring (why one port)

ANC needs the speaker output and the mic input on **one clock**, so the speaker-to-mic delay is
fixed. The firmware opens I2S0 once in full duplex (TX and RX from one `i2s_new_channel` call):

```
             GPIO15 BCLK ──┬──────────────┬──────────────┐
             GPIO16 WS   ──┼──┬───────────┼──┬───────────┼──┐
ESP32-S3     GPIO17 DOUT ──┼──┼───────────┼──┼──────┐    │  │
             GPIO14 DIN  ──┼──┼──┐        │  │      │    │  │
                           │  │  │        │  │      │    │  │
                        SCK WS SD      SCK WS SD   DIN BCLK LRC
                        INMP441 #1     INMP441 #2   MAX98357A
                        L/R=GND        L/R=3V3      GAIN, SD open
                        (reference)    (error)      (mono L+R/2)
```

- 16 kHz, 32-bit stereo slots, Philips I2S. Mic data is 24-bit, MSB-aligned (`>> 8`).
- Both INMP441s drive the same SD line. Each one goes high-impedance in the other's slot, which
  is the datasheet's stereo arrangement.
- The firmware writes the same sample to both slots, so the MAX98357A (SD pin open = (L+R)/2)
  plays it.
- **Deviation from the brief:** the brief asked for `ESP_I2S`. The firmware uses the ESP-IDF
  `driver/i2s_std.h` API instead, which `ESP_I2S` wraps. `ESP_I2S` fixes the DMA at 6 × 240
  frames, about 90 ms each way. That large delay makes the speaker-path phase change by roughly
  30° per Hz, so the path model breaks as the motor speed drifts. The IDF API allows 3 × 64
  frames (about 12 ms). Same clocking, same pins.

## Power

- **The amplifier gets its own 5 V supply.** Powering the MAX98357A from the ESP32-S3's USB 5 V
  browned out the board in the classic-ESP32 build: speaker current peaks pulled VBUS down and
  the ESP32 reset. Use a separate 5 V regulator (≥ 1 A) or battery for the MAX98357A Vin, and
  **join all grounds** (amp, ESP32, motor supply, sensors) at one point.
- The motor has its own supply too. Drive it through a logic-level MOSFET (gate from GPIO7 via
  ~100 Ω, 10 kΩ gate pull-down), with a flyback diode across the motor. Keep motor current out of
  the signal ground (star ground).
- 3V3 from the DevKitC feeds the INMP441s, ADXL345, DS18B20 and the pull-ups.
- The A3144 needs **4.5–24 V**. Power it from 5 V, but pull its output up to **3V3** only.
- The ACS712 runs on 5 V. Its output (2.5 V at 0 A, ±185 mV/A) goes through the 2/3 divider into
  GPIO4. **Never connect it to GPIO4 directly.**
- Motor off at boot: the current sensor zeroes itself during the first ~0.1 s.

## USB / serial

- Board setting "ESP32S3 Dev Module". Plug into the **UART** USB port (USB-to-UART bridge),
  921600 baud. If you use the native USB port, enable "USB CDC On Boot" (the baud rate is then
  ignored).
- Close the Arduino Serial Monitor before starting the backend: only one program can open the
  port.
- Libraries: OneWire and DallasTemperature (already installed here). Without them the sketch
  still builds and reports `temp_c: null`.

## Bring-up and calibration

1. **Self-test.** Flash `quietdrive_s3_selftest`. Open the Serial Monitor at 921600 with
   "Newline", motor off, room quiet. It checks I2S, both mics (stuck or silent lines), the
   speaker-to-error-mic path, the mic slot order, the ADXL345 rate (~1600/s) and gravity (~1 g),
   the ACS712 zero (~1.67 V), the DS18B20, the buzzer and RGB, and, only after you type `y`, the
   motor, Hall rpm and load current. Fix every FAIL before going on.
2. **Flash `quietdrive_s3`.** In `backend/.env` set `SOURCE=serial`, `SERIAL_PORT=COMx`,
   `BAUD=921600` and start the backend. At connect, the backend sends the model's `whine_order`.
3. **Check the whine order.** Run the motor and watch `order_meas` on the dashboard ANC card
   (whine Hz ÷ Hall shaft Hz). If it doesn't match `whine_order`, set it:
   `POST /api/device/set {"whine_order": N}`. The firmware saves it to flash. Orders below 2 are
   refused: order 1 is the protected shaft tone.
4. **Calibrate the speaker path.** Run the motor at the speed you'll demo, then **stop it** and
   press **Calibrate** (or `POST /api/device/anc/calibrate`). The board plays tones for about 9 s
   at 17 points across ±20 % around the last running whine frequency and its 2nd harmonic. It
   estimates the fixed I2S delay and saves the table to flash. The LED blinks magenta while it
   runs. Keep the room quiet. If it reports "path too weak", move the speaker closer to the error
   mic or turn the gain up.
5. **ANC on.** Start the motor and press **ANC ON**. The target frequency follows the Hall rpm
   every 4 ms block. If the speed leaves the calibrated ±20 % band, the card shows "OUTSIDE
   CALIBRATED RANGE": that channel fades out until you recalibrate at the new speed.
6. **Tuning.** `anc_mu` (adaptation rate, default 0.01, range 0.0005–0.1) and `anc_vmax` (output
   limit, default 0.6). If the card shows AT LIMIT, the speaker is too weak or too far away.

### ANC behaviour, for reference

- **Two narrowband FxLMS channels:** whine = shaft × `whine_order`, plus its 2nd harmonic.
  Algorithm from the proven classic-ESP32 sketch: block-based update, `makeRef()` rotation
  oscillator, weight clamp at `anc_vmax`, louder-than-baseline guard (4 windows of 0.5 s that are
  3 dB worse → weights reset).
- **"Hybrid" in this design** means: feedforward from a reference *synthesised* from the Hall
  shaft speed, plus feedback adaptation from the error mic. The reference mic isn't in the
  control loop. It's used for diagnosis features, the raw waterfall, and to normalise the
  reduction figure: reduction = change in (error-mic tone ÷ reference-mic tone) against the
  ANC-off baseline, so a motor that gets louder or quieter doesn't read as ANC.
- **Protected tones:** the 1x shaft tone is never generated. Any channel within 8 Hz of 1x, or
  of a tone the backend lists in `inf.protected_hz` (e.g. 2x for loose_mount, stored as a shaft
  order so it follows the speed), is muted.
- **Motor stopped** (Hall rpm < 300): no ANC output, and the backend reports `fault: "stopped"`
  without running the model.

## Features computed on the device vs `bridge/common.py`

Feature names and order are the same 53 `FEATURE_NAMES` the models use. Differences:

| Feature | `bridge/common.py` (phone) | ESP32-S3 firmware |
|---|---|---|
| audio prefilter | `sosfiltfilt` HP 50 Hz + LP 7 kHz, order 4 | same Butterworth biquads, forward + backward with 512-sample odd-extension padding (scipy pads 15 samples and uses steady-state initial conditions; edge differences only) |
| audio spectrum / 32 bands / `whine_hz` / `audio_rms_db` | Welch 1024, hop 512, Hann, 1 s | identical method, reference mic. **Level scale differs**: INMP441 dBFS vs laptop mic dBFS |
| shaft speed | `whine_hz / WHINE_ORDER` (estimated) | **Hall sensor** (exact). Falls back to whine/order if the Hall signal is lost (`rpm_src: "whine"`) |
| vibration source | phone, ~200 Hz, irregular, resampled | ADXL345 1600 Hz FIFO, uniform |
| vibration prefilter | bandpass 3 Hz…0.45·fs (one 8th-order bandpass design) | HP 3 Hz + LP 720 Hz as two separate order-4 Butterworths, zero phase. Nearly identical for these edges |
| 16 vibration bands | 2 Hz…0.9·Nyquist ≈ 2–90 Hz | 2 Hz…0.9·Nyquist = **2–720 Hz** |
| `vib_1x` / `vib_2x` | max of 3 bins around whine-derived shaft | same rule, around the **Hall** shaft speed, 1 s Hann zero-padded to 2048 (0.78 Hz grid) |
| `vib_rms` | RMS of the band-passed signal (≤ 90 Hz) | RMS up to 720 Hz, so it picks up much more high-frequency energy |
| extras (not model inputs) | — | `centroid_hz`, `i_mean`, `i_ripple`, `temp_c`, `shaft_hz` |

Because of these differences, **the phone-trained model can't be trusted on hardware features**.
The backend uses it as a stopgap and every `inf` carries
`model_note: "Phone-trained model: needs retraining on hardware recordings"` (shown on the
dashboard). Loudness-based extras (dB over healthy, `fix_db`, `fix_vib_pct`) are switched off
until a model trained on ESP32-S3 data is loaded.

## Recording and retraining on hardware data

```powershell
# backend running with SOURCE=serial; ANC off; motor at the test condition
py -3.12 bridge/record_esp32.py --label normal --seconds 90
py -3.12 bridge/record_esp32.py --label imbalance --level 2 --speed 1 --seconds 90
# ... at least 2 sessions per label, so whole sessions can be held out for testing
py -3.12 bridge/train.py --data bridge/data_esp32s3          # -> bridge/models_esp32s3/
```

Sessions go to `bridge/data_esp32s3/<label>_L<level>_S<speed>_<time>/` in the `record.py` folder
format: `audio.wav` (reference mic, 16 kHz), `accel.csv` (`t_s,x_g,y_g,z_g`, 1600 Hz),
`meta.json` (`source: "esp32s3"`, sensor rates, dropped samples), plus `features.jsonl` (the
firmware's own feature frames) and `telemetry.csv` (Hall rpm, current, temperature).

`train.py` trains on `features.jsonl` by default, so training and live use the same feature code.
Venue-noise augmentation needs raw audio, so it's off in that mode. `--features python`
recomputes the features from the raw files (Hall rpm as shaft speed) and allows augmentation.
Phone and ESP32-S3 sessions are never mixed unless you pass `--allow-mix`. The backend loads
`bridge/models_esp32s3/quietdrive_model.joblib` first and falls back to the phone model.

## What still needs hardware validation

Nothing below has been run on a real board yet.

- [ ] Both sketches link and flash for "ESP32S3 Dev Module" (DRAM use: ~50 KB static arrays + ~180 KB
      heap buffers allocated at boot; the S3 has ~320 KB internal RAM free under Arduino, so it
      should fit, but a "FATAL: out of memory" log line means it didn't).
- [ ] I2S full duplex on the S3 with two INMP441s on one SD line: left slot = reference mic,
      right slot = error mic (the self-test "Mic slot order" check).
- [ ] Fixed I2S latency (3 × 64-frame DMA) is stable across resets; the measured `anc_tau_ms` is
      repeatable.
- [ ] Path calibration: table magnitude above 0.002 at all points, and delay removal makes the
      residual phase smooth enough for linear interpolation (17 points over ±20 %).
- [ ] ANC actually reduces the whine at the error mic while the speed drifts. **No dB figure is
      claimed until measured.**
- [ ] Louder-than-baseline guard and protected-tone muting behave as intended at real speeds.
- [ ] Audio task timing: `audio_load_pct` in `tel` stays well below 100 % (the 4 ms budget).
- [ ] ADXL345 at 1600 Hz over I2C: core 0 drains the 32-sample FIFO in time during feature
      computation (no overflows); the I2C transactions take an estimated 30–40 % of core 0. If
      not, move the sensor to SPI.
- [ ] Serial bandwidth at 921600 baud: ~25 KB/s normally, ~75 KB/s while recording raw. Check
      that `dropped_audio_samples` is 0 in `meta.json`.
- [ ] Hall: `MAGNETS`, pull-up and debounce (300 µs) at the top speed. `order_meas` matches the
      whine order.
- [ ] ACS712 zero and scale with the real divider; `i_ripple` is sampled far below the 20 kHz
      PWM, so it's only a rough figure.
- [ ] DS18B20 non-blocking reads at 1 Hz.
- [ ] Spread-spectrum PWM (±15 % hop every 2 ms) doesn't upset the motor driver or the LED/buzzer
      timers. `pwm_mode: "optimize"` is accepted but behaves as fixed (not implemented).
- [ ] Brown-out-free operation with the amp on its own 5 V supply.
- [ ] Fault model retrained on ESP32-S3 recordings, with whole sessions held out for the test.
