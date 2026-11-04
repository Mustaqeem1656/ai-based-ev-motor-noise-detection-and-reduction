#include "motor.h"

namespace quietdrive {

MotorController::MotorController() = default;

void MotorController::begin() {
  pinMode(MOTOR_PIN, OUTPUT);
  ledcAttachPin(MOTOR_PIN, 0);
  ledcSetup(0, PWM_BASE_HZ, 8);
  enabled_ = true;
  setDuty(0);
}

void MotorController::setDuty(uint8_t duty) {
  duty_ = constrain(duty, MOTOR_DUTY_MIN, MOTOR_DUTY_MAX);
  const uint32_t duty_value = (duty_ * 255u) / 100u;
  ledcWrite(0, duty_value);
}

void MotorController::setMode(PwmMode mode) {
  mode_ = mode;
  if (mode_ == PwmMode::Fixed) {
    pwm_hz_ = static_cast<uint32_t>(PWM_BASE_HZ);
    ledcSetup(0, pwm_hz_, 8);
  } else if (mode_ == PwmMode::Spread) {
    pwm_hz_ = static_cast<uint32_t>(PWM_BASE_HZ * (1.0f + (random(-15, 16) / 100.0f)));
    ledcSetup(0, pwm_hz_, 8);
  } else {
    pwm_hz_ = static_cast<uint32_t>(PWM_BASE_HZ);
    ledcSetup(0, pwm_hz_, 8);
  }
}

void MotorController::update() {
  if (!enabled_) return;
  if (mode_ == PwmMode::Spread) {
    static uint32_t last_switch_ms = 0;
    const uint32_t now = millis();
    if (now - last_switch_ms > 20) {
      pwm_hz_ = static_cast<uint32_t>(PWM_BASE_HZ * (1.0f + (random(-15, 16) / 100.0f)));
      ledcSetup(0, pwm_hz_, 8);
      last_switch_ms = now;
    }
  }
  const uint32_t duty_value = (duty_ * 255u) / 100u;
  ledcWrite(0, duty_value);
}

uint8_t MotorController::duty() const {
  return duty_;
}

uint32_t MotorController::pwmHz() const {
  return pwm_hz_;
}

}  // namespace quietdrive
