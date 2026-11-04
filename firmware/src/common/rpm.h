#pragma once

#include <Arduino.h>

#include "config.h"

namespace quietdrive {

class HallSensor {
 public:
  HallSensor();
  void begin();
  void update();
  float rpm() const;
  uint32_t pulseCount() const;

 private:
  static void onPulse();
  static volatile uint32_t last_pulse_us_;
  static volatile uint32_t pulse_count_;
  float rpm_ = 0.0f;
};

}  // namespace quietdrive
