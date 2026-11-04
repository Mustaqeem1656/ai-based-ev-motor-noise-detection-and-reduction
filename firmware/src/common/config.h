#pragma once

#include <Arduino.h>

namespace quietdrive {

constexpr uint32_t SERIAL_BAUD = 921600;
constexpr uint32_t AUDIO_SAMPLE_RATE = 16000;
constexpr uint16_t AUDIO_FFT_SIZE = 1024;
constexpr uint16_t VIBRATION_FFT_SIZE = 512;
constexpr uint8_t SPECTRUM_BIN_COUNT = 128;
constexpr uint8_t REFERENCE_MIC = 0;
constexpr uint8_t ERROR_MIC = 1;
constexpr float MAX_CURRENT_A = 5.0f;
constexpr uint32_t TELEMETRY_INTERVAL_MS = 100;
constexpr uint32_t SPECTRUM_INTERVAL_MS = 200;
constexpr uint32_t INFERENCE_INTERVAL_MS = 1000;
constexpr uint32_t FEATURE_INTERVAL_MS = 500;
constexpr uint32_t HALL_TIMEOUT_MS = 1000;
constexpr float PWM_BASE_HZ = 4000.0f;
constexpr float PWM_SPREAD_PERCENT = 0.15f;
constexpr uint8_t MOTOR_DUTY_MIN = 0;
constexpr uint8_t MOTOR_DUTY_MAX = 100;
constexpr char VEHICLE_ID[] = "QD-01";
constexpr float MOTOR_CURRENT_ZERO_VOLTAGE = 1.65f;
constexpr float ACS712_SENSITIVITY_MV_PER_A = 185.0f;
constexpr float DIVIDER_RATIO = 2.0f / 3.0f;

enum class PwmMode : uint8_t {
  Fixed = 0,
  Spread = 1,
  Optimize = 2,
};

inline const char* pwmModeToString(PwmMode mode) {
  switch (mode) {
    case PwmMode::Fixed:
      return "fixed";
    case PwmMode::Spread:
      return "spread";
    case PwmMode::Optimize:
      return "optimize";
    default:
      return "fixed";
  }
}

inline PwmMode pwmModeFromString(const String& value) {
  if (value == "spread") return PwmMode::Spread;
  if (value == "optimize") return PwmMode::Optimize;
  return PwmMode::Fixed;
}

}  // namespace quietdrive
