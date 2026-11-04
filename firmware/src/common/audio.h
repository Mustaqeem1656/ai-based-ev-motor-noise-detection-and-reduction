#pragma once

#include <Arduino.h>

#include "config.h"

namespace quietdrive {

class AudioProcessor {
 public:
  AudioProcessor();
  void begin();
  void update();
  float referenceRmsDb() const;
  float errorRmsDb() const;
  void getSpectrum(float* ref_bins, float* err_bins, size_t count);

 private:
  float reference_rms_db_ = 0.0f;
  float error_rms_db_ = 0.0f;
  float ref_bins_[128] = {0};
  float err_bins_[128] = {0};
  bool enabled_ = false;
};

}  // namespace quietdrive
