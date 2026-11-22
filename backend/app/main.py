from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine
from app.ingest.hardware import start_hardware
from app.ingest.simulator import simulator
from app.routers import anc, demo, device, recordings, vehicles, websocket

Base.metadata.create_all(bind=engine)

app = FastAPI(title="QuietDrive API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vehicles.router)
app.include_router(demo.router)
app.include_router(recordings.router)
app.include_router(websocket.router)
app.include_router(anc.router)
app.include_router(device.router)


@app.on_event("startup")
async def startup_event() -> None:
    if settings.source == "sim":            # simulator; bridge/live.py can still stream (pauses it per vehicle)
        await simulator.start()
    elif settings.source == "serial":       # hardware edition: ESP32-S3 on SERIAL_PORT
        await start_hardware()
    # "bridge": software edition only, no simulator


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await simulator.stop()


if settings.serve_frontend:
    dist_dir = Path(__file__).resolve().parents[1].parent / "frontend" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")


@app.get("/")
def root() -> dict:
    return {"service": "QuietDrive", "status": "running"}
