"""Hardware edition: ESP32-S3 over USB serial (SOURCE=serial).

  device --tel/spec/feat/ack/log/raw--> HardwareLink --> event bus --> /ws/live/{vid}
                                          |  feat -> Inference (Random Forest models) -> inf
  device <--inf / cmd--------------------'  (inf also published to the dashboard)

The serial port is read on a thread (pyserial blocks) and frames are handed to the asyncio loop.
Raw recording frames (raw/rawv) go only to the Recorder, never to the dashboard.
"""
import asyncio
import json
import logging
import threading
import time
from typing import Optional

from app.analytics.inference import Inference, load_model
from app.config import settings
from app.ingest.event_bus import bus
from app.ingest.processing import process_frame
from app.ingest.recorder import Recorder

log = logging.getLogger("quietdrive.hardware")

DEVICE_INF_KEYS = ("fault", "tier", "health", "protected_hz", "severity_raw")


def now_ms() -> int:
    return int(time.time() * 1000)


def parse_line(line: str) -> Optional[dict]:
    if not line or not line.startswith("{"):
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        log.warning("Malformed serial frame ignored: %s", line[:200])
        return None
    return payload if isinstance(payload, dict) and "t" in payload else None


class HardwareLink:
    def __init__(self, port: str, baud: int, vid: str, model=None, recorder: Optional[Recorder] = None):
        self.port, self.baud, self.vid = port, baud, vid
        self.ser = None
        self.wlock = threading.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.inference = Inference(model)
        self.recorder = recorder or Recorder(settings.esp32_data_dir)
        self.connected = False
        self.last_tel: dict = {}
        self.last_ack: Optional[dict] = None
        self.last_log: Optional[str] = None
        self.frames = 0
        self._stop = threading.Event()

    # ---------------- lifecycle ----------------
    async def start(self):
        self.loop = asyncio.get_running_loop()
        threading.Thread(target=self._reader, name="esp32s3-serial", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _open(self):
        import serial
        self.ser = serial.Serial(self.port, self.baud, timeout=0.5)
        self.connected = True
        log.info("ESP32-S3 on %s @ %d", self.port, self.baud)
        order = settings.whine_order or (self.inference.model or {}).get("whine_order")
        if order and float(order) >= 2:
            self.send({"cmd": "set", "whine_order": float(order)})

    def _reader(self):
        while not self._stop.is_set():
            try:
                if self.ser is None or not self.ser.is_open:
                    self._open()
                raw = self.ser.readline()
                if not raw:
                    continue
                msg = parse_line(raw.decode("utf-8", errors="ignore").strip())
                if msg is not None and self.loop is not None:
                    asyncio.run_coroutine_threadsafe(self.handle(msg), self.loop)
            except Exception as exc:  # pragma: no cover - needs hardware
                self.connected = False
                log.warning("Serial %s: %s (retrying)", self.port, exc)
                try:
                    if self.ser:
                        self.ser.close()
                except Exception:
                    pass
                self.ser = None
                time.sleep(2)

    # ---------------- outgoing ----------------
    def send(self, obj: dict) -> bool:
        if self.ser is None:
            return False
        try:
            with self.wlock:
                self.ser.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())
            return True
        except Exception as exc:  # pragma: no cover
            log.warning("Serial write failed: %s", exc)
            return False

    # ---------------- incoming ----------------
    async def handle(self, msg: dict):
        self.frames += 1
        kind = msg.get("t")
        msg.setdefault("vid", self.vid)
        self.recorder.feed(msg)                      # uses the device clock (ts), before it is replaced
        if self.recorder.due():
            res = self.recorder.stop(); self.send({"cmd": "set", "raw": False})
            await bus.publish({"t": "rec", "vid": msg["vid"], "ts": now_ms(), **res})
        if kind in ("raw", "rawv"):
            return
        msg["ts_dev"] = msg.get("ts"); msg["ts"] = now_ms()
        bus.mark_device(msg["vid"])
        if kind == "tel":
            self.last_tel = msg
        elif kind == "ack":
            self.last_ack = msg
        elif kind == "log":
            self.last_log = msg.get("msg"); log.info("[ESP32-S3] %s", self.last_log)
        await bus.publish(process_frame(msg))
        if kind == "feat":
            inf = self.inference.on_feat(msg)
            if inf is not None:
                frame = {"t": "inf", "ts": now_ms(), "vid": msg["vid"], "src": "esp32s3", **inf}
                await bus.publish(process_frame(frame))
                self.send({"t": "inf", **{k: frame[k] for k in DEVICE_INF_KEYS if k in frame}})

    def status(self) -> dict:
        t = self.last_tel
        return dict(source="serial", port=self.port, connected=self.connected, frames=self.frames,
                    model=(self.inference.model or {}).get("path"), model_source=(self.inference.model or {}).get("source"),
                    anc_cal=t.get("anc_cal"), anc_on=t.get("anc_on"), last_ack=self.last_ack, last_log=self.last_log,
                    recording=self.recorder.status())


link: Optional[HardwareLink] = None


async def start_hardware() -> HardwareLink:
    global link
    link = HardwareLink(settings.serial_port, settings.baud, settings.vid, load_model())
    await link.start()
    return link
