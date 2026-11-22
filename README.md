# QuietDrive

QuietDrive is a lightweight EV motor noise detection, severity grading, and noise-reduction platform built for a laptop-based demo. The backend exposes a FastAPI API and WebSocket feeds, and the frontend is a premium React + Tailwind dashboard that consumes the live telemetry.

## Two editions

The backend and dashboard are the same for both. `SOURCE` in `backend/.env` picks the input.

| | Hardware edition | Software edition (fallback) |
|---|---|---|
| sensing | one **ESP32-S3 DevKitC-1**: 2× INMP441 (reference + error mic), ADXL345 1600 Hz, A3144 Hall rpm, ACS712 current, DS18B20 temp | laptop mic + phone accelerometer (phyphox) |
| ANC | on the board: fault-aware narrowband FxLMS, reference from the Hall rpm every 4 ms, whine + 2nd harmonic, 1x shaft tone protected | ANC on recordings (`bridge/anc_recorded.py`, simulated speaker path), optional classic-ESP32 ANC via `live.py --esp32` |
| link | USB serial 921600, `SOURCE=serial` | WebSocket `/ws/device/QD-01` from `bridge/live.py`, `SOURCE=sim` or `SOURCE=bridge` |
| model | `bridge/models_esp32s3/` (train on hardware recordings), else the phone model as a stopgap, flagged **"needs retraining on hardware recordings"** | `bridge/models/` |
| status | firmware compile-checked only; **not yet validated on hardware**, no measured accuracy or dB figures yet | working (demo build) |

Hardware wiring, power, calibration and the validation checklist: [docs/hardware.md](docs/hardware.md).

### Run the hardware edition (ESP32-S3)

```powershell
# 1) flash firmware/quietdrive_s3_selftest (Arduino IDE, board "ESP32S3 Dev Module"), fix every FAIL
# 2) flash firmware/quietdrive_s3, close the Serial Monitor
# 3) backend/.env:  SOURCE=serial   SERIAL_PORT=COM5   BAUD=921600
cd backend; py -3.12 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
cd frontend; npm run dev            # http://localhost:5173
# 4) dashboard, section 03: motor at demo speed -> stop -> CALIBRATE -> motor on -> ANC ON
# 5) record hardware training data (ANC off), then retrain:
py -3.12 bridge/record_esp32.py --label normal --seconds 90
py -3.12 bridge/record_esp32.py --label imbalance --level 2 --seconds 90
py -3.12 bridge/train.py --data bridge/data_esp32s3      # -> bridge/models_esp32s3 (restart the backend)
```

### Run the software edition (fallback)

Unchanged, see "Demo day" below: `SOURCE=sim` (or `SOURCE=bridge` for no simulator) and
`bridge/live.py`.

## Repository layout

- backend/: FastAPI app, simulator, ESP32-S3 serial link (`app/ingest/hardware.py`), analytics, recorder
- frontend/: Vite + React + TypeScript dashboard
- firmware/quietdrive_s3/: ESP32-S3 hardware-edition firmware (Arduino sketch); firmware/quietdrive_s3_selftest/: sensor self-test
- firmware/src, firmware/platformio.ini: the earlier PlatformIO scaffold (superseded by the sketches above)
- docs/protocol.md: serial/WiFi protocol; docs/hardware.md: pins, wiring, power, calibration
- bridge/: laptop mic + phone accelerometer sensor bridge (software edition), training, see bridge/README.md

## Demo day (software edition)

Three terminals, all from the repo root (Python is `py -3.12`).

```powershell
# once
py -3.12 -m pip install -r backend/requirements.txt -r bridge/requirements.txt
cd frontend; npm install; cd ..

# 1) backend
cd backend; py -3.12 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2) dashboard -> http://localhost:5173
cd frontend; npm run dev

# 3a) no hardware: synthetic motor (keys 0-3 = fault, +/- = level)
py -3.12 bridge/fake_dataset.py
py -3.12 bridge/train.py --data bridge/data_fake
py -3.12 bridge/live.py --fake

# 3b) real rig: laptop mic + phone (phyphox remote access)
py -3.12 bridge/record.py --phone 192.168.43.20:8080 --check
py -3.12 bridge/train.py
py -3.12 bridge/live.py --phone 192.168.43.20:8080 --mic N

# 4) live noise removal demo (headphones on). Restart step 3 with --no-spec first,
#    so the raw waterfall shows only the cleaner's frames.
py -3.12 bridge/clean_voice.py --mic N          # keys: c cleaned, r raw, o playback, s save 10 s
#    no hardware: py -3.12 bridge/live.py --fake --no-spec  +  py -3.12 bridge/clean_voice.py --fake --play

# 5) ANC on recordings: dashboard > "Noise reduction" tab > pick a session > Run ANC
#    (real recorded motor noise, simulated speaker path). CLI: py -3.12 bridge/anc_recorded.py bridge/data/<session>
```

Any device on the `/ws/device/QD-01` endpoint pauses the simulator for QD-01 while it streams.
The ESP32-S3 hardware edition uses serial instead (`SOURCE=serial`, see above).

## Requirements

- Python 3.10+
- Node.js 18+
- npm (or pnpm/yarn)
- Git

## Quick start

### Option A: run backend and frontend separately

Windows PowerShell:

```powershell
cd quietdrive\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env

cd ..\frontend
npm install
Copy-Item .env.example .env
```

macOS / Linux:

```bash
cd quietdrive/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

cd ../frontend
npm install
cp .env.example .env
```

### Backend run

Start the API and simulator:

Windows PowerShell:

```powershell
cd quietdrive\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

macOS / Linux:

```bash
cd quietdrive/backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend run

Windows PowerShell:

```powershell
cd quietdrive\frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

macOS / Linux:

```bash
cd quietdrive/frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

Open the UI at http://localhost:5173.

### Option B: serve the frontend from the FastAPI backend

Set this in backend/.env:

```env
SOURCE=sim
SERVE_FRONTEND=true
```

Then run only the backend:

Windows PowerShell:

```powershell
cd quietdrive\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

macOS / Linux:

```bash
cd quietdrive/backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open http://localhost:8000.

## Switching to the ESP32-S3 runtime

Set SOURCE=serial in backend/.env and point SERIAL_PORT to your device:

- Windows: COM3, COM4, etc.
- macOS: /dev/cu.usbserial-XXXX or /dev/tty.usbserial-XXXX
- Linux: /dev/ttyUSB0, /dev/ttyACM0

Example:

```env
SOURCE=serial
SERIAL_PORT=COM3
BAUD=921600
```

or

```env
SOURCE=serial
SERIAL_PORT=/dev/ttyUSB0
BAUD=921600
```

If the port is missing or disconnected, the backend logs a warning and retries every 2 s. The
simulator doesn't run with `SOURCE=serial`. Device controls: `GET /api/device/status`,
`POST /api/device/anc {"on":true}`, `POST /api/device/anc/calibrate`, `POST /api/device/set {...}`,
`POST /api/device/record/start|stop`.

## Recorded sessions and model retraining

To start a labelled recording session from the UI or API:

```bash
curl -X POST http://localhost:8000/api/recordings/start -H "Content-Type: application/json" -d '{"vid":"QD-01","fault_type":"imbalance","severity_label":"Moderate"}'
```

To stop it:

```bash
curl -X POST http://localhost:8000/api/recordings/stop
```

Recorded CSV data is written to backend/data/recordings/ so it can be used for Edge Impulse ingestion or local model retraining.

Retraining command:

```bash
cd quietdrive/backend
python ml/train.py
```

## Exporting data for Edge Impulse

Use the CSV files saved in backend/data/recordings/ and export them in the format expected by your Edge Impulse project. Keep the same timestamp, spectrum, and telemetry columns, or pre-process them into a single labeled dataset if you want to train a new on-device model.

## Troubleshooting

- If the frontend cannot connect to the backend, confirm VITE_API_URL in frontend/.env matches the Python API URL.
- If the backend fails to detect serial ports, verify the exact port name from your OS device manager or terminal listing.
- If there is no hardware, keep SOURCE=sim and the app will run with the built-in simulator by default.

## Notes

- The backend is intentionally lightweight and does not use TensorFlow or PyTorch.
- The simulator is the default runtime and is built to work with no hardware connected.
- The UI is designed for a projector demo and supports a laptop or tablet layout.
