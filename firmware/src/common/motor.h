#pragma once

#include <Arduino.h>

#include "config.h"

namespace quietdrive {

class MotorController {
 public:
  MotorController();
  void begin();
  void setDuty(uint8_t duty);
  void setMode(PwmMode mode);
  void update();
  uint8_t duty() const;
  uint32_t pwmHz() const;

 private:
  uint8_t duty_ = 0;
  uint32_t pwm_hz_ = 4000;
  PwmMode mode_ = PwmMode::Fixed;
  bool enabled_ = false;
};

}  // namespace quietdrive
