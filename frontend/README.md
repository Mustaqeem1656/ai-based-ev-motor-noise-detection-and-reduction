# QuietDrive Frontend

This folder contains the premium vehicle dashboard for QuietDrive.

## Setup

Windows PowerShell:

```powershell
cd quietdrive\frontend
npm install
Copy-Item .env.example .env
```

macOS / Linux:

```bash
cd quietdrive/frontend
npm install
cp .env.example .env
```

## Environment

Edit `.env`:

```env
VITE_API_URL=http://localhost:8000
```

## Run

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

Open http://localhost:5173.

## Build for production

```bash
npm run build
```

If you want the backend to serve the built frontend bundle, set `SERVE_FRONTEND=true` in backend/.env and run the backend as usual.
