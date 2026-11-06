# QuietDrive

QuietDrive is a lightweight EV motor noise detection, severity grading, and noise-reduction platform built for a laptop-based demo. The backend exposes a FastAPI API and WebSocket feeds, and the frontend is a premium React + Tailwind dashboard that consumes the live telemetry.

## Repository layout

- backend/: FastAPI app, simulator, analytics, SQLite storage, training code
- frontend/: Vite + React + TypeScript dashboard
- firmware/: PlatformIO ESP32-S3 firmware project (Arduino framework) for the ECU
- docs/protocol.md: exact serial/WiFi protocol that the firmware must follow
- bridge/: laptop mic + phone accelerometer sensor bridge (stand-in for the ESP32), see bridge/README.md

## Demo day

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
```

The ESP32 path is unchanged: it connects to the same `/ws/device/QD-01` endpoint
(or serial with `SOURCE=serial`). While any device streams for QD-01 the simulator pauses QD-01.

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
BAUD=115200
```

or

```env
SOURCE=serial
SERIAL_PORT=/dev/ttyUSB0
BAUD=115200
```

If the port is missing or disconnected, the backend logs a reconnect warning and keeps the simulator alive as a fallback if SOURCE is not set to serial.

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
