# QuietDrive protocol

This document defines the exact messages exchanged between the ESP32-S3 ECU and the QuietDrive backend.

## Transport

- Serial over USB: newline-delimited JSON at 921600 baud (ESP32-S3 hardware edition, `SOURCE=serial`;
  see [Hardware edition (ESP32-S3)](#hardware-edition-esp32-s3) at the end for its extra fields and commands)
- WiFi/WebSocket: ws://<host>:8000/ws/device/{vehicle_id}
- The backend must ignore malformed JSON lines and never crash on bad input.

## Backend -> ECU commands

The backend sends commands over the same channel:

```json
{"cmd":"set","anc":true,"pwm_mode":"spread","vib_cancel":true,"motor_duty":62}
```

Field definitions:

- cmd: must be "set"
- anc: boolean, enables/disables active noise cancellation
- pwm_mode: one of "fixed", "spread", or "optimize"
- vib_cancel: boolean, enables vibration canceling
- motor_duty: integer 0-100

## ECU -> backend telemetry

Telemetry is published around 10 Hz:

```json
{"t":"tel","ts":1710000000000,"vid":"QD-01","rpm":1450.0,"current_a":9.6,"temp_c":72.2,"vib_rms_g":1.85,"vib_xyz":[0.4,0.9,1.2],"spl_ref_db":74.5,"spl_err_db":66.2,"pwm_hz":16000,"pwm_mode":"spread","anc_on":true,"vib_cancel_on":true,"vib_cancel_effort":0.42}
```

Field definitions:

- t: telemetry packet type, always "tel"
- ts: millisecond timestamp
- vid: vehicle identifier
- rpm: motor RPM
- current_a: phase current in amps
- temp_c: motor temperature in Celsius
- vib_rms_g: RMS vibration in g
- vib_xyz: vibration in x/y/z axis
- spl_ref_db: reference microphone sound pressure level in dB
- spl_err_db: error microphone level in dB
- pwm_hz: PWM switching frequency in Hz
- pwm_mode: one of "fixed", "spread", "optimize"
- anc_on: whether ANC is active
- vib_cancel_on: whether vibration cancellation is active
- vib_cancel_effort: normalized effort 0-1

## ECU -> backend spectrum frame

Spectrum frames are published around 5 Hz:

```json
{"t":"spec","ts":1710000000000,"vid":"QD-01","mic":"err","fmax":4000,"bins":[128.0,127.5,126.1, ...]}
```

Field definitions:

- t: packet type, always "spec"
- ts: millisecond timestamp
- vid: vehicle identifier
- mic: "ref" or "err"
- fmax: max spectral frequency in Hz
- bins: list of 128 floating-point dB values
- reduction_db (optional, mic "err" only): dB removed in the notched whine bands (raw vs cleaned)
- whine_hz (optional, mic "err" only): tracked motor whine frequency
- notch_hz (optional, mic "err" only): notch frequencies currently active

## ECU -> backend feature vector

Feature frames are published every 0.5 s:

```json
{"t":"feat","ts":1710000000000,"vid":"QD-01","rpm":1800.0,"audio_bands":[32.0,30.2,29.7,...],"vib_bands":[16.0,15.3,15.8,...],"vib_1x":1.2,"vib_2x":0.8,"i_mean":9.6,"i_ripple":1.1,"temp_c":72.1}
```

Field definitions:

- t: packet type, always "feat"
- ts: millisecond timestamp
- vid: vehicle identifier
- rpm: motor RPM
- audio_bands: array of 32 log-spaced band energies in dB, up to 8 kHz
- vib_bands: array of 16 vibration band energies
- vib_1x: amplitude at 1x motor speed
- vib_2x: amplitude at 2x motor speed
- i_mean: mean current in amps
- i_ripple: AC ripple RMS current in amps
- temp_c: temperature in Celsius

## ECU -> backend on-chip inference

On-chip inference is published around 1 Hz:

```json
{"t":"inf","ts":1710000000000,"vid":"QD-01","fault":"imbalance","conf":{"audio":0.81,"vib":0.93,"current":0.68},"severity_raw":0.71,"protected_hz":[150,300,450]}
```

Field definitions:

- t: packet type, always "inf"
- ts: millisecond timestamp
- vid: vehicle identifier
- fault: one of "normal", "imbalance", "loose_mount", or "bearing"
- conf: dict of sensor confidences, each 0-1
- severity_raw: normalized severity 0-1
- protected_hz: list of frequencies that ANC is not canceling

Optional owner-advice fields (added for the dashboard; old devices may omit them, and the
backend then computes them from fault, severity_raw, conf and the vehicle's healthy loudness):

```json
{"points":56,"health":44,"tier":"MODERATE","action":"service_soon","advice":"Get it checked at a service centre within a week. Quick check: ...","reason":"Shaking once per motor turn (1x shaft speed), typical of an imbalance; 4 dB louder than when healthy.","sensors_agree":true,"fix_db":3.2}
```

- points: problem points 0-100, higher = worse. Severity level (0-3) scaled to 75, plus 1.5 per dB
  louder than the healthy baseline (max 15); x0.7 unless audio and vib confidence are both >= 0.5;
  any confirmed fault scores at least 21
- health: 100 - points
- tier: "HEALTHY" (0-20), "MILD" (21-45), "MODERATE" (46-75), "SEVERE" (76-100)
- action: "ok", "diy", "service_soon" or "service_now" (matches the tier)
- advice: one-line instruction for the owner, with a fault-specific tip for MILD / MODERATE
- reason: why, in plain words, including how many dB louder than when healthy
- sensors_agree: true when sound and vibration confidence are both >= 0.5
- fix_vib_pct (optional): expected % less vibration once fixed (vs healthy vibration); sent when >= 5 %. The dashboard shows it when fix_db is missing or below 0.5 dB
- fix_db (optional): expected dB reduction once the fault is fixed, learned from recordings

## Serial command acknowledgment

The ECU acknowledges valid commands with an ack line:

```json
{"t":"ack","ok":true,"reason":"command applied"}
```

If parsing fails, the ECU may respond with `{"t":"ack","ok":false,"reason":"malformed json"}`.

## Optional values and other devices

Any device may stream on `/ws/device/{vehicle_id}`. The laptop bridge (`bridge/live.py`,
laptop mic + phone accelerometer) uses exactly these messages; values it cannot measure
(`current_a`, `temp_c`, `spl_err_db`, `pwm_hz`, `i_mean`, `i_ripple`, `temp_c`, `conf.current`)
are sent as `null`. Consumers must treat `null` as "not available", not as zero.
While a device streams for a vehicle, the backend simulator pauses that vehicle.

## Parsing rules

The backend parser must be tolerant and safe:

- Ignore empty lines.
- If a line is malformed JSON, log it and continue.
- If required fields are missing, skip the record but do not crash.
- If a timestamp or float fails to parse, discard the packet.
- The parser should accept both serial and WebSocket frames using the same JSON decode path.

## Message examples

### Valid
a:

```json
{"t":"tel","ts":1000,"vid":"QD-01","rpm":1200.0,"current_a":10.0,"temp_c":80.0,"vib_rms_g":1.5,"vib_xyz":[0.2,0.4,0.7],"spl_ref_db":74.0,"spl_err_db":66.0,"pwm_hz":16000,"pwm_mode":"spread","anc_on":true,"vib_cancel_on":true,"vib_cancel_effort":0.44}
```

### Invalid

```json
{"t":"tel", "ts": "n/a", "vid": "QD-01"}
```

This packet should be discarded and logged as malformed.

## Hardware edition (ESP32-S3)

Firmware `firmware/quietdrive_s3`, backend `SOURCE=serial` (`backend/app/ingest/hardware.py`).
All messages above still apply. The additions are listed here. The backend replaces `ts` with
its own wall-clock milliseconds and keeps the device's `millis()` as `ts_dev`.

### Backend -> ESP32-S3

```json
{"cmd":"set","pwm_mode":"spread","motor_duty":40,"anc":true,"anc_mu":0.01,"anc_vmax":0.6,"whine_order":21}
{"cmd":"set","raw":true}
{"cmd":"anc_calibrate","f_hz":1250}
{"cmd":"anc_abort"}  {"cmd":"anc_reset"}  {"cmd":"ping"}
{"t":"inf","fault":"imbalance","tier":"MILD","health":70,"protected_hz":[59.6],"severity_raw":0.33}
```

- `set` fields are all optional:
  - `anc`: refused (ack `ok:false`) until the speaker path is calibrated.
  - `anc_mu`: 0.0005–0.1.
  - `anc_vmax`: 0–1.
  - `whine_order`: must be ≥ 2, because order 1 is the protected shaft tone. Saved to flash.
  - `pwm_mode`: `fixed` = 20 kHz; `spread` = ±15 % random hop every 2 ms; `optimize` is accepted
    and behaves as `fixed`.
  - `motor_duty`: 0–100 %.
  - `raw`: switches on the recording stream (see below).
- `anc_calibrate`: speaker-to-error-mic path table, about 9 s; the motor must be stopped.
  `f_hz` is the centre, default the last whine frequency seen while running.
- `inf` is the compact diagnosis sent back to the board after each `feat`. The board uses `tier`
  for the RGB LED and `protected_hz` to mute any ANC channel near those tones. They're stored as
  shaft orders, so they follow the speed.
- Every command is answered with `{"t":"ack","ok":...,"reason":"..."}`.

### ESP32-S3 -> backend

`tel` (10 Hz) adds:

| field | meaning |
|---|---|
| `src` | `"esp32s3"` |
| `rpm_src` | `"hall"` |
| `stopped` | Hall rpm below 300 |
| `motor_duty` | % |
| `anc_on` | ANC requested |
| `anc_active` | at least one channel is outputting |
| `anc_freq`, `anc_freq2` | target Hz (whine and 2nd harmonic), recomputed from the Hall rpm every 4 ms |
| `anc_reduction_db`, `anc_reduction2_db` | drop in the error/reference-mic tone ratio against the ANC-off baseline, per 0.5 s window; `null` when that channel is inactive |
| `anc_err_db` | whine-tone level at the error mic, dBFS |
| `anc_out` | total output amplitude, 0–1 |
| `anc_at_limit` | a channel is clamped at `anc_vmax` |
| `anc_out_of_range` | target outside the calibrated ±20 % band |
| `anc_mu`, `anc_vmax` | current settings |
| `anc_cal` | `none` / `running` / `ok` / `failed` |
| `anc_cal_pct`, `anc_tau_ms`, `anc_cal_center_hz`, `anc_msg` | calibration progress and status |
| `protected_hz` | the 1x shaft tone plus the backend's protected tones, live Hz |
| `whine_order` | in use |
| `order_meas` | measured whine_hz ÷ Hall shaft Hz |
| `audio_load_pct` | core-1 compute time per 4 ms block |
| `raw` | raw recording stream is on |

- `spl_ref_db` / `spl_err_db` are dBFS + 100 (uncalibrated, comparable over time, same
  convention as the bridge).
- `current_a`, `temp_c` and `vib_rms_g` are real readings; they're `null` when the sensor is
  missing.

`spec` (5 Hz; 2.5 Hz while `raw` is on) is sent for `mic:"ref"` and `mic:"err"`. The `err` frame
is the real error-mic spectrum and adds `reduction_db`, `whine_hz` and `anc_hz` (the active ANC
targets).

`feat` (2 Hz, 1 s window) adds the model inputs that weren't listed before, plus extras:

- `vib_rms`, `audio_rms_db`, `whine_hz`: model inputs.
- `shaft_hz`, `rpm_src` (`hall`, or `whine` when the Hall signal is lost), `centroid_hz`,
  `vib_ok`, `feat_v`.

Feature differences from `bridge/common.py` are listed in `docs/hardware.md`.

`log`: `{"t":"log","msg":"..."}`, human-readable status (boot report, calibration result,
guard resets).

`raw` / `rawv` (10 Hz, only while `raw` is on, for recording training sessions):

```json
{"t":"raw","seq":12,"fs":16000,"i0":192000,"t0_us":12000123,"gap":0,"a":"<base64 int16 LE, reference mic>"}
{"t":"rawv","seq":12,"fs":1600,"i0":19200,"t_last_us":12099800,"gap":0,"g_per_lsb":0.0039,"v":"<base64 int16 LE x,y,z interleaved>"}
```

- `i0` is the index of the first sample.
- `t0_us` / `t_last_us` are device `esp_timer` microseconds; `millis()` uses the same base.
- `gap` counts samples the board had to skip.
- These frames go only to the recorder (`/api/device/record/*`), never to the dashboard.

### Backend -> dashboard (`/ws/live/{vid}`)

- `inf` from the hardware path carries `src: "esp32s3"`, `model_source` (`"esp32s3"` or
  `"phone"`) and `model_note`. While no hardware-trained model is loaded, `model_note` is
  `"Phone-trained model: needs retraining on hardware recordings"`.
- With Hall rpm < `STOPPED_RPM`, `inf.fault` is `"stopped"` and the model isn't run.
- `rec` is published when a recording ends: `{"t":"rec","ok":true,"folder":"...","seconds":90.0,...}`.
