#include "rpm.h"

namespace quietdrive {

volatile uint32_t HallSensor::last_pulse_us_ = 0;
volatile uint32_t HallSensor::pulse_count_ = 0;

HallSensor::HallSensor() = default;

void HallSensor::begin() {
  pinMode(HALL_SENSOR_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(HALL_SENSOR_PIN), onPulse, FALLING);
  last_pulse_us_ = micros();
  pulse_count_ = 0;
}

void HallSensor::update() {
  const uint32_t now = micros();
  if ((now - last_pulse_us_) > (HALL_TIMEOUT_MS * 1000UL)) {
    rpm_ = 0.0f;
    return;
  }

  const uint32_t interval_us = now - last_pulse_us_;
  if (interval_us == 0) {
    rpm_ = 0.0f;
    return;
  }
  rpm_ = (60.0e6f / static_cast<float>(interval_us));
}

float HallSensor::rpm() const {
  return rpm_;
}

uint32_t HallSensor::pulseCount() const {
  return pulse_count_;
}

void HallSensor::onPulse() {
  const uint32_t now = micros();
  if (last_pulse_us_ == 0 || now - last_pulse_us_ > 10000UL) {
    pulse_count_ += 1;
  }
  last_pulse_us_ = now;
}

}  // namespace quietdrive
