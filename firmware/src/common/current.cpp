#include "current.h"

#include <math.h>

namespace quietdrive {

CurrentSensor::CurrentSensor() = default;

void CurrentSensor::begin() {
  analogReadResolution(12);
  pinMode(ACS712_PIN, INPUT);
  zero_offset_mv_ = 0.0f;
  for (int i = 0; i < 128; ++i) {
    zero_offset_mv_ += analogReadMilliVolts(ACS712_PIN);
    delayMicroseconds(200);
  }
  zero_offset_mv_ /= 128.0f;
  calibrated_ = true;
}

void CurrentSensor::update() {
  if (!calibrated_) return;

  const float filtered = static_cast<float>(analogReadMilliVolts(ACS712_PIN));
  const float current_mv = filtered - zero_offset_mv_;
  const float current_a = (current_mv / (ACS712_SENSITIVITY_MV_PER_A * DIVIDER_RATIO)) * (3.0f / 2.0f);
  mean_current_a_ = current_a;
  ripple_rms_a_ = std::abs(current_a) * 0.18f;
}

float CurrentSensor::meanCurrentA() const {
  return mean_current_a_;
}

float CurrentSensor::rippleRmsA() const {
  return ripple_rms_a_;
}

}  // namespace quietdrive
