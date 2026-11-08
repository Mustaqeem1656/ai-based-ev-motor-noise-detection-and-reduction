"""
QuietDrive - ESP32 ANC link (laptop <-> ESP32 over USB serial).

What it does:
  * reads the ESP32 ANC status lines and keeps the latest values in .state
    (merge .state into the dashboard 'inf' message)
  * frequency tracking: after you run 'd' (or 'f600') on the ESP32, it scales that
    frequency with the motor speed measured by live.py and sends 't<hz>' updates,
    so the ANC follows the motor as it speeds up / slows down
  * console: type ESP32 commands (d, f600, p, a, m0.03, v0.6) in the live.py window;
    type 'track' to switch speed tracking on/off

Needs: py -3.12 -m pip install pyserial
Close the Arduino Serial Monitor first (only one program can use the COM port).
"""
import re, threading, time

try:
    import serial  # pyserial
except ImportError:
    serial = None

_STATUS = re.compile(
    r"ANC (ON|OFF)\s*\|\s*([\d.]+) Hz at mic\s+(-?[\d.]+) dB \| reduction\s+(-?[\d.]+) dB \| out ([\d.]+)(.*)")
_WHINE = re.compile(r"Whine at ([\d.]+) Hz")


class ESP32Link:
    def __init__(self, port, baud=115200, console=True):
        if serial is None:
            raise RuntimeError("pyserial missing: py -3.12 -m pip install pyserial")
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self.lock = threading.Lock()
        self.state = {"anc_connected": True, "anc_on": False, "anc_freq": None, "anc_level_db": None,
                      "anc_reduction_db": None, "anc_out": None, "anc_at_limit": False,
                      "anc_tracking": False, "anc_msg": "ESP32 connected"}
        self.track = False
        self._rpm = None           # smoothed rpm from live.py
        self._ref_f = None         # frequency set by 'd' or 'f'
        self._ref_rpm = None       # rpm at that moment
        self._sent = None
        threading.Thread(target=self._reader, daemon=True).start()
        if console:
            threading.Thread(target=self._console, daemon=True).start()

    # ---------- outgoing ----------
    def send(self, cmd):
        with self.lock:
            self.ser.write((cmd.strip() + "\n").encode())

    def update_rpm(self, rpm):
        """Call every cycle from live.py with the current rpm estimate."""
        if rpm is None or rpm < 600:
            return
        self._rpm = rpm if self._rpm is None else 0.8 * self._rpm + 0.2 * rpm   # smooth estimator jitter
        if self.track and self._ref_f and self._ref_rpm:
            f = self._ref_f * self._rpm / self._ref_rpm
            if self._sent is None or abs(f - self._sent) > 0.3:
                self.send(f"t{f:.2f}")
                self._sent = f

    # ---------- incoming ----------
    def _reader(self):
        while True:
            try:
                line = self.ser.readline().decode(errors="ignore").strip()
            except Exception as e:
                self.state.update(anc_connected=False, anc_msg=f"serial error: {e}")
                time.sleep(1); continue
            if not line:
                continue
            m = _STATUS.match(line)
            if m:
                self.state.update(anc_on=m.group(1) == "ON", anc_freq=float(m.group(2)),
                                  anc_level_db=float(m.group(3)), anc_reduction_db=float(m.group(4)),
                                  anc_out=float(m.group(5)), anc_at_limit="LIMIT" in m.group(6),
                                  anc_tracking=self.track)
                continue
            print(f"\n[ESP32] {line}")
            self.state["anc_msg"] = line
            w = _WHINE.search(line)
            if w:
                self._set_ref(float(w.group(1)))

    def _set_ref(self, f):
        self._ref_f, self._ref_rpm, self._sent = f, self._rpm, f

    # ---------- console ----------
    def _console(self):
        print("[ESP32] link ready. Type: d | f600 | p | a | m0.03 | v0.6 | track")
        while True:
            try:
                cmd = input().strip()
            except EOFError:
                return
            if not cmd:
                continue
            if cmd == "track":
                self.track = not self.track
                self.state["anc_tracking"] = self.track
                if self.track and not self._ref_rpm:
                    print("[ESP32] tracking needs a reference: run 'd' (or f<hz>) while the motor runs")
                print(f"[ESP32] speed tracking {'ON' if self.track else 'OFF'}")
                continue
            if cmd.startswith("f"):
                try:
                    self._set_ref(float(cmd[1:]))
                except ValueError:
                    pass
            self.send(cmd)


if __name__ == "__main__":   # standalone test: py -3.12 bridge/esp32_link.py COM5
    import sys
    link = ESP32Link(sys.argv[1] if len(sys.argv) > 1 else "COM5")
    while True:
        time.sleep(1)
        s = link.state
        if s["anc_freq"]:
            print(f"\r ANC {'ON ' if s['anc_on'] else 'OFF'} {s['anc_freq']:.1f} Hz  red {s['anc_reduction_db']:5.1f} dB  out {s['anc_out']:.2f}   ", end="")
