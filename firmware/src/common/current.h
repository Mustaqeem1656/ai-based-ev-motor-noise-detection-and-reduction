#pragma once

#include <Arduino.h>

#include "config.h"

namespace quietdrive {

class CurrentSensor {
 public:
  CurrentSensor();
  void begin();
  void update();
  float meanCurrentA() const;
  float rippleRmsA() const;

 private:
  float mean_current_a_ = 0.0f;
  float ripple_rms_a_ = 0.0f;
  float zero_offset_mv_ = 0.0f;
  bool calibrated_ = false;
};

}  // namespace quietdrive
