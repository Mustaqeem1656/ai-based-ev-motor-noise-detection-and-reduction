# QuietDrive Firmware

This folder contains the PlatformIO project for the ESP32-S3 ECU.

## Install PlatformIO

1. Install VS Code.
2. Install the PlatformIO extension from the VS Code marketplace.
3. Open this folder in VS Code.
4. Let PlatformIO import the project automatically.

## Build and flash the selftest environment

1. In the PlatformIO sidebar, choose the `selftest` environment.
2. Select `Upload`.
3. Open the serial monitor at 921600 baud.
4. Read the pass/fail report. If a test fails, fix the issue before continuing.

The selftest environment runs the hardware validation sequence described in the project brief and prints a summary table. It is meant to help diagnose wiring and sensor issues before the main firmware is deployed.

## Build and flash the main environment

1. Switch the PlatformIO environment to `main`.
2. Upload the code to the ESP32-S3 board.
3. Keep the serial monitor open at 921600 baud.
4. Verify that telemetry, spectra, and inference lines flow in the exact format defined in [../docs/protocol.md](../docs/protocol.md).

## Connect the firmware to the backend

The laptop backend must be running before the device is plugged in or started. In backend/.env, set the source to serial or wifi as needed:

```env
SOURCE=serial
SERIAL_PORT=COM3
BAUD=921600
```

On macOS/Linux:

```env
SOURCE=serial
SERIAL_PORT=/dev/ttyUSB0
BAUD=921600
```

When the firmware is connected to the backend over USB serial, the backend listens for newline-delimited JSON payloads in the protocol format described in [../docs/protocol.md](../docs/protocol.md).

## Notes

- The project uses the Arduino framework for ESP32-S3.
- The `selftest` environment is intentionally separate from the recording firmware.
- The `main` environment includes the ECU telemetry, spectrum generation, PWM control, and serial command handling.
- Phase C modules (`anc.cpp`, `vibcancel.cpp`, and `model.cpp`) are intentionally left as TODO stubs for the next firmware iteration.
