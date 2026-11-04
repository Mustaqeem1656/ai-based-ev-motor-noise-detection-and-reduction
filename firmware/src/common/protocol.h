#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

#include "config.h"

namespace quietdrive {

struct Command {
  bool anc = false;
  bool vib_cancel = false;
  String pwm_mode = "fixed";
  uint8_t motor_duty = 0;
  float health = 100.0f;
};

class Protocol {
 public:
  static void begin(Stream& stream);
  static bool readCommand(Command& command);
  static void sendAck(bool ok, const String& reason = "");
  static void sendTelemetry(float rpm,
                            float current_a,
                            float temp_c,
                            float vib_rms_g,
                            const float* vib_xyz,
                            float spl_ref_db,
                            float spl_err_db,
                            uint32_t pwm_hz,
                            const String& pwm_mode,
                            bool anc_on,
                            bool vib_cancel_on,
                            float vib_cancel_effort);
  static void sendSpectrum(uint32_t ts,
                          const String& mic,
                          float fmax,
                          const float* bins,
                          size_t bin_count);
  static void sendInference(const String& fault,
                           const float* conf,  // 3 values
                           float severity_raw,
                           const uint16_t* protected_hz,
                           size_t protected_count);
  static void sendFeature(float rpm,
                          const float* audio_bands,
                          size_t audio_band_count,
                          const float* vib_bands,
                          size_t vib_band_count,
                          float vib_1x,
                          float vib_2x,
                          float i_mean,
                          float i_ripple,
                          float temp_c);
};

}  // namespace quietdrive
