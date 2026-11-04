#pragma once

#include <Arduino.h>

#include "config.h"

namespace quietdrive {

class VibrationSensor {
 public:
  VibrationSensor();
  void begin();
  void update();
  float rmsG() const;
  void xyz(float* x, float* y, float* z) const;
  float dominant1x() const;
  float dominant2x() const;

 private:
  float rms_g_ = 0.0f;
  float x_ = 0.0f;
  float y_ = 0.0f;
  float z_ = 0.0f;
  float vib_1x_ = 0.0f;
  float vib_2x_ = 0.0f;
  bool enabled_ = false;
};

}  // namespace quietdrive
