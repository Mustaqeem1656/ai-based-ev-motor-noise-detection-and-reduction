import json

from app.ingest.serial_reader import SerialReader


def test_parser_accepts_valid_json_line():
    parser = SerialReader(port="COM3")
    payload = {
        "t": "tel",
        "ts": 1000,
        "vid": "QD-01",
        "rpm": 1200.0,
        "current_a": 9.0,
        "temp_c": 78.0,
        "vib_rms_g": 1.1,
        "vib_xyz": [0.1, 0.2, 0.3],
        "spl_ref_db": 70.0,
        "spl_err_db": 64.0,
        "pwm_hz": 16000,
        "pwm_mode": "spread",
        "anc_on": True,
        "vib_cancel_on": True,
        "vib_cancel_effort": 0.3,
    }
    parsed = parser._parse_line(json.dumps(payload))
    assert parsed == payload


def test_parser_ignores_malformed_lines():
    parser = SerialReader(port="COM3")
    assert parser._parse_line("not json") is None
    assert parser._parse_line("") is None
