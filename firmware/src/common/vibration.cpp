#include "vibration.h"

#include <Adafruit_ADXL345.h>
#include <cmath>

namespace quietdrive {

namespace {
Adafruit_ADXL345_Unified g_accel = Adafruit_ADXL345_Unified(12345);
}

VibrationSensor::VibrationSensor() = default;

void VibrationSensor::begin() {
  if (!g_accel.begin()) {
    enabled_ = false;
    return;
  }
  g_accel.setRange(ADXL345_RANGE_2_G);
  g_accel.setDataRate(ADXL345_DATARATE_1600_HZ);
  enabled_ = true;
}

void VibrationSensor::update() {
  if (!enabled_) return;

  sensors_event_t event;
  g_accel.getEvent(&event);
  x_ = event.acceleration.x;
  y_ = event.acceleration.y;
  z_ = event.acceleration.z;
  rms_g_ = std::sqrt(x_ * x_ + y_ * y_ + z_ * z_);
  vib_1x_ = std::max(0.0f, x_ * 0.4f + y_ * 0.2f);
  vib_2x_ = std::max(0.0f, z_ * 0.25f + y_ * 0.18f);
}

float VibrationSensor::rmsG() const {
  return rms_g_;
}

void VibrationSensor::xyz(float* x, float* y, float* z) const {
  if (x != nullptr) *x = x_;
  if (y != nullptr) *y = y_;
  if (z != nullptr) *z = z_;
}

float VibrationSensor::dominant1x() const {
  return vib_1x_;
}

float VibrationSensor::dominant2x() const {
  return vib_2x_;
}

}  // namespace quietdrive
