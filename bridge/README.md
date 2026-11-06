# QuietDrive bridge (laptop mic + phone accelerometer)

Stand-in sensors while the ESP32-S3 is out: the laptop microphone and a phone
accelerometer (phyphox). `live.py` connects to the backend exactly like the ESP32
does (`ws://localhost:8000/ws/device/QD-01`, same `tel`/`spec`/`feat`/`inf` messages),
so the backend and dashboard need no special handling. Values the bridge cannot
measure (current, temperature, error mic) are sent as `null`.

All commands are run from the **repo root**.

| File | What it does |
|---|---|
| common.py | feature extraction (same as firmware), the PREFILTER, phyphox client, fake motor |
| record.py | record one labelled session -> bridge/data/<label>_L<level>_S<speed>_<time>/ |
| train.py | train fault / severity / per-sensor models -> bridge/models/ |
| live.py | stream live data + predictions to the backend |
| fake_dataset.py | synthetic sessions in bridge/data_fake (test without a motor) |
| plot_prefilter.py | spectrum before vs after prefiltering (PNG for slides) |

## 0. Install (once)
    py -3.12 -m pip install -r bridge/requirements.txt

Windows: Settings > System > Sound > your mic > turn OFF "Audio enhancements" / noise suppression.

## 1. Test without any hardware
    py -3.12 bridge/fake_dataset.py
    py -3.12 bridge/train.py --data bridge/data_fake
    py -3.12 bridge/live.py --fake          # keys 0-3 change the fault, +/- the level

## 2. Phone (phyphox)
1. Open **Acceleration (without g)** > three dots > **Enable remote access**. Note the address.
2. Phone and laptop on the same WiFi / hotspot. Tape the phone flat and tight to the motor mount.
3. `py -3.12 bridge/record.py --phone 192.168.43.20:8080 --check` (prints the phone sample rate).
   The 2x shaft frequency must stay below 0.45 x that rate; run the motor slower if not.

## 3. Record (90 s each; at least 2 sessions per label for an honest score)
    py -3.12 bridge/record.py --list-mics
    py -3.12 bridge/record.py --phone ADDR --mic N --label normal --level 0 --speed 1
    py -3.12 bridge/record.py --phone ADDR --mic N --label imbalance --level 2 --speed 1
    ... loose_mount / bearing, levels 1-3, speeds 1-3

## 4. Train and run live
    py -3.12 bridge/train.py
    py -3.12 bridge/live.py --phone ADDR --mic N
    py -3.12 bridge/plot_prefilter.py bridge/data/<session>      # optional slide

## Prefilter (one place: common.feature_row)
- Audio: zero-phase Butterworth high-pass 50 Hz + low-pass 7 kHz, order 4.
- Vibration: resampled to uniform time, gravity/DC removed, band-pass 3 Hz to 0.45 x sample rate.
Recordings are saved raw, so the filter can be changed and the models retrained.
