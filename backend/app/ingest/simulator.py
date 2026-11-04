import asyncio
import math
import time
from typing import Dict, List

from app.ingest.event_bus import bus

VEHICLES = ["QD-01", "QD-02", "QD-03", "QD-04"]
FAULTS = ["normal", "imbalance", "loose_mount", "bearing"]


class Simulator:
    def __init__(self) -> None:
        self.base_states: Dict[str, dict] = {
            "QD-01": {"rpm": 1800, "fault": "normal", "severity": 0.08, "current_a": 11.0, "temp_c": 68.0},
            "QD-02": {"rpm": 1650, "fault": "normal", "severity": 0.12, "current_a": 10.0, "temp_c": 66.0},
            "QD-03": {"rpm": 2100, "fault": "normal", "severity": 0.1, "current_a": 12.4, "temp_c": 71.0},
            "QD-04": {"rpm": 1450, "fault": "normal", "severity": 0.15, "current_a": 9.3, "temp_c": 64.0},
        }
        self.state: Dict[str, dict] = {k: dict(v) for k, v in self.base_states.items()}
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def set_fault(self, vid: str, fault: str, severity: float, ramp_minutes: float = 0.0) -> None:
        if vid not in self.state:
            return
        if fault not in FAULTS:
            fault = "normal"
        self.state[vid]["fault"] = fault
        self.state[vid]["severity"] = max(0.0, min(severity, 1.0))
        self.state[vid]["ramp_minutes"] = float(ramp_minutes)

    def reset(self) -> None:
        self.state = {k: dict(v) for k, v in self.base_states.items()}

    async def _run(self) -> None:
        t = 0
        while self._running:
            t += 1
            for vid in VEHICLES:
                state = self.state[vid]
                fault = state["fault"]
                severity = state["severity"]
                rpm = state["rpm"]
                amplitude = max(0.0, severity)

                motor_drag = 1.0 + 0.3 * math.sin(t / 16.0 + len(vid))
                rpm_wave = rpm * motor_drag
                current = state["current_a"] + 0.25 * math.sin(t / 10.0 + len(vid))
                temp = state["temp_c"] + 0.08 * t / 10.0 + (severity * 18.0 if fault != "normal" else 0.0)

                vib_rms = 0.5 + severity * 2.4
                if fault == "imbalance":
                    vib_rms += 1.2 + 1.5 * severity
                elif fault == "loose_mount":
                    vib_rms += 1.7 + 1.8 * severity
                elif fault == "bearing":
                    vib_rms += 1.4 + 1.5 * severity

                spl_ref = 65.0 + 6.0 * math.sin(t / 18.0) + severity * 18.0
                spl_err = spl_ref - 9.0 + (4.0 * severity if fault == "normal" else 2.0 * severity)
                if fault == "loose_mount":
                    spl_err += 4.0 + 8.5 * severity
                if fault == "bearing":
                    spl_err += 3.0 + 9.0 * severity

                if fault == "imbalance":
                    vib_rms += 0.7 * severity

                anc_active = True if severity > 0.16 else False
                vib_cancel_active = True if severity > 0.22 else False
                vib_cancel_effort = min(1.0, max(0.0, severity * 1.4))

                demo_telemetry = {
                    "t": "tel",
                    "ts": int(time.time() * 1000),
                    "vid": vid,
                    "rpm": round(rpm_wave, 2),
                    "current_a": round(current, 3),
                    "temp_c": round(temp, 2),
                    "vib_rms_g": round(vib_rms, 3),
                    "vib_xyz": [round(vib_rms * 0.35, 3), round(vib_rms * 0.52, 3), round(vib_rms * 0.75, 3)],
                    "spl_ref_db": round(spl_ref, 2),
                    "spl_err_db": round(spl_err, 2),
                    "pwm_hz": 16000,
                    "pwm_mode": "spread" if t % 5 == 0 else "fixed",
                    "anc_on": anc_active,
                    "vib_cancel_on": vib_cancel_active,
                    "vib_cancel_effort": round(vib_cancel_effort, 3),
                }
                await bus.publish(demo_telemetry)

                if t % 2 == 0:
                    bins = [max(-30, 40 + 18 * math.sin(i / 10.0) + math.sin(i / 7.0) + (severity * 12.0)) for i in range(128)]
                    if fault == "imbalance":
                        bins[18] += 15 * severity
                        bins[25] += 9 * severity
                    if fault == "loose_mount":
                        bins[32] += 18 * severity
                        bins[47] += 12 * severity
                    if fault == "bearing":
                        bins[80] += 22 * severity
                        bins[96] += 18 * severity
                    await bus.publish({
                        "t": "spec",
                        "ts": int(time.time() * 1000),
                        "vid": vid,
                        "mic": "ref",
                        "fmax": 4000,
                        "bins": bins,
                    })
                    await bus.publish({
                        "t": "spec",
                        "ts": int(time.time() * 1000),
                        "vid": vid,
                        "mic": "err",
                        "fmax": 4000,
                        "bins": [b + (6 if anc_active else 0) for b in bins],
                    })

                if t % 10 == 0:
                    conf = {"audio": 0.3 + severity * 0.6, "vib": 0.4 + severity * 0.5, "current": 0.2 + severity * 0.5}
                    if fault == "normal":
                        conf = {"audio": 0.18, "vib": 0.16, "current": 0.14}
                    await bus.publish({
                        "t": "inf",
                        "ts": int(time.time() * 1000),
                        "vid": vid,
                        "fault": fault,
                        "conf": conf,
                        "severity_raw": round(min(1.0, severity + 0.15), 3),
                        "protected_hz": [max(1, int((rpm_wave / 60.0) * 2)), max(1, int((rpm_wave / 60.0) * 3))],
                    })

            await asyncio.sleep(0.1)


simulator = Simulator()
