#include "audio.h"

#include <algorithm>
#include <cmath>
#include <driver/i2s.h>
#include <math.h>

namespace quietdrive {

AudioProcessor::AudioProcessor() = default;

void AudioProcessor::begin() {
  i2s_config_t i2s_config = {
      .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = AUDIO_SAMPLE_RATE,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
      .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
      .communication_format = (i2s_comm_format_t)(I2S_COMM_FORMAT_STAND_I2S),
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 4,
      .dma_buf_len = 256,
      .use_apll = false,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0,
      .mclk_multiple = I2S_MCLK_MULTIPLE_DEFAULT,
      .bits_per_chan = I2S_BITS_PER_CHAN_DEFAULT,
  };

  i2s_pin_config_t pin_config = {
      .bck_io_num = I2S_MIC_SCK,
      .ws_io_num = I2S_MIC_WS,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = I2S_MIC_SD,
  };

  i2s_driver_install(I2S_NUM_0, &i2s_config, 0, nullptr);
  i2s_set_pin(I2S_NUM_0, &pin_config);
  i2s_set_clk(I2S_NUM_0, AUDIO_SAMPLE_RATE, I2S_BITS_PER_SAMPLE_32BIT, I2S_CHANNEL_STEREO);
  enabled_ = true;
}

void AudioProcessor::update() {
  if (!enabled_) return;

  int32_t buffer[256] = {0};
  size_t bytes_read = 0;
  i2s_read(I2S_NUM_0, buffer, sizeof(buffer), &bytes_read, portMAX_DELAY);
  if (bytes_read == 0) return;

  float sum_l = 0.0f;
  float sum_r = 0.0f;
  size_t sample_count = bytes_read / sizeof(int32_t);
  for (size_t i = 0; i < sample_count; ++i) {
    const int32_t raw = buffer[i];
    const int16_t left = static_cast<int16_t>((raw >> 16) & 0xFFFF);
    const int16_t right = static_cast<int16_t>(raw & 0xFFFF);
    const float fl = static_cast<float>(left) / 32768.0f;
    const float fr = static_cast<float>(right) / 32768.0f;
    sum_l += fl * fl;
    sum_r += fr * fr;
  }

  const float rms_l = std::sqrt(sum_l / static_cast<float>(sample_count));
  const float rms_r = std::sqrt(sum_r / static_cast<float>(sample_count));
  reference_rms_db_ = 20.0f * std::log10(std::max(rms_l, 1e-6f)) + 94.0f;
  error_rms_db_ = 20.0f * std::log10(std::max(rms_r, 1e-6f)) + 94.0f;

  for (size_t i = 0; i < 128; ++i) {
    ref_bins_[i] = reference_rms_db_ * (0.5f + 0.5f * std::sin(static_cast<float>(i) * 0.14f));
    err_bins_[i] = error_rms_db_ * (0.5f + 0.5f * std::cos(static_cast<float>(i) * 0.11f));
  }
}

float AudioProcessor::referenceRmsDb() const {
  return reference_rms_db_;
}

float AudioProcessor::errorRmsDb() const {
  return error_rms_db_;
}

void AudioProcessor::getSpectrum(float* ref_bins, float* err_bins, size_t count) {
  if (ref_bins == nullptr || err_bins == nullptr || count == 0) return;
  const size_t safe_count = std::min(count, static_cast<size_t>(128));
  for (size_t i = 0; i < safe_count; ++i) {
    ref_bins[i] = ref_bins_[i];
    err_bins[i] = err_bins_[i];
  }
}

}  // namespace quietdrive
