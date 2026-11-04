#pragma once

#include <Arduino.h>

#include "config.h"

namespace quietdrive {

class TemperatureSensor {
 public:
  TemperatureSensor();
  void begin();
  void update();
  float celsius() const;

 private:
  float temp_c_ = 0.0f;
  bool enabled_ = false;
};

}  // namespace quietdrive
