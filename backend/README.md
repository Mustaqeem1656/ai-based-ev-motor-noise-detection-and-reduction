# QuietDrive Backend

This folder contains the Python FastAPI backend for QuietDrive.

## Setup

Windows PowerShell:

```powershell
cd quietdrive\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux:

```bash
cd quietdrive/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Environment

Edit .env before running:

```env
SOURCE=sim
SERIAL_PORT=COM3
BAUD=115200
DB_URL=sqlite:///./quietdrive.db
SERVE_FRONTEND=false
CORS_ORIGINS=http://localhost:5173,http://localhost:8000
```

- SOURCE can be sim, serial, or wifi.
- SERIAL_PORT is OS-specific: COM3 on Windows, /dev/ttyUSB0 on Linux, /dev/cu.usbserial-xxxx on macOS.
- SERVE_FRONTEND=true serves the built frontend bundle from the backend when you want a single-process demo.

## Run

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

## API examples

Health:

```bash
curl http://localhost:8000/api/health
```

Vehicles:

```bash
curl http://localhost:8000/api/vehicles
```

Inject a simulated fault:

```bash
curl -X POST http://localhost:8000/api/sim/fault \
  -H "Content-Type: application/json" \
  -d '{"vid":"QD-01","fault":"imbalance","severity":0.7,"ramp_minutes":3}'
```

Reset the simulator:

```bash
curl -X POST http://localhost:8000/api/sim/reset
```

## Training

```bash
cd quietdrive/backend
python ml/train.py
```
