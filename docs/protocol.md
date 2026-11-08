# QuietDrive protocol

This document defines the exact messages exchanged between the ESP32-S3 ECU and the QuietDrive backend.

## Transport

- Serial over USB: newline-delimited JSON at 115200 baud (or other configured rate)
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
