from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # sim    : built-in simulator; the software edition (bridge/live.py) can still stream over
    #          /ws/device/{vid} and pauses the simulator for that vehicle
    # bridge : no simulator, only devices on /ws/device/{vid} (software edition, laptop mic + phone)
    # serial : hardware edition, ESP32-S3 on SERIAL_PORT (firmware/quietdrive_s3)
    source: str = "sim"
    serial_port: str = "COM3"
    baud: int = 921600
    vid: str = "QD-01"                       # vehicle the serial device reports as
    model_path: Optional[str] = None         # default: bridge/models_esp32s3, else bridge/models (phone)
    whine_order: Optional[float] = None      # sent to the device at connect; default = the model's
    stopped_rpm: float = 300.0               # Hall rpm below this = motor stopped, model skipped
    esp32_data_dir: str = str(REPO / "bridge" / "data_esp32s3")
    db_url: str = "sqlite:///./quietdrive.db"
    serve_frontend: bool = False
    cors_origins: str = "http://localhost:5173,http://localhost:8000"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins_list(self) -> List[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
