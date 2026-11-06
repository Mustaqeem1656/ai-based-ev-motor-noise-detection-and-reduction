"""Server-side enrichment applied to every frame, whatever the source (ESP32, bridge, serial)."""


def process_frame(payload: dict) -> dict:
    return payload
