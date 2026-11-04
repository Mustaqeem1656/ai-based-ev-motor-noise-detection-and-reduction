#include "protocol.h"

namespace quietdrive {

namespace {
Stream* g_stream = nullptr;
}

void Protocol::begin(Stream& stream) {
  g_stream = &stream;
}

bool Protocol::readCommand(Command& command) {
  if (g_stream == nullptr) return false;
  if (!g_stream->available()) return false;

  String line = g_stream->readStringUntil('\n');
  if (line.length() == 0) return false;
  line.trim();
  if (line.length() == 0) return false;

  DynamicJsonDocument doc(512);
  DeserializationError err = deserializeJson(doc, line);
  if (err) {
    sendAck(false, "malformed json");
    return false;
  }

  if (!doc.containsKey("cmd") || String(doc["cmd"].as<const char*>()) != "set") {
    return false;
  }

  if (doc.containsKey("anc") && doc["anc"].is<bool>()) {
    command.anc = doc["anc"].as<bool>();
  }
  if (doc.containsKey("vib_cancel") && doc["vib_cancel"].is<bool>()) {
    command.vib_cancel = doc["vib_cancel"].as<bool>();
  }
  if (doc.containsKey("pwm_mode") && doc["pwm_mode"].is<const char*>()) {
    command.pwm_mode = doc["pwm_mode"].as<const char*>();
  }
  if (doc.containsKey("motor_duty") && doc["motor_duty"].is<int>()) {
    command.motor_duty = constrain(doc["motor_duty"].as<int>(), 0, 100);
  }
  if (doc.containsKey("health") && doc["health"].is<float>()) {
    command.health = constrain(doc["health"].as<float>(), 0.0f, 100.0f);
  }

  sendAck(true, "command applied");
  return true;
}

void Protocol::sendAck(bool ok, const String& reason) {
  if (g_stream == nullptr) return;
  DynamicJsonDocument doc(128);
  doc["t"] = "ack";
  doc["ok"] = ok;
  doc["reason"] = reason;
  serializeJson(doc, *g_stream);
  *g_stream << "\n";
}

void Protocol::sendTelemetry(float rpm,
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
                            float vib_cancel_effort) {
  if (g_stream == nullptr) return;
  DynamicJsonDocument doc(512);
  doc["t"] = "tel";
  doc["ts"] = millis();
  doc["vid"] = VEHICLE_ID;
  doc["rpm"] = rpm;
  doc["current_a"] = current_a;
  doc["temp_c"] = temp_c;
  doc["vib_rms_g"] = vib_rms_g;
  JsonArray xyz = doc["vib_xyz"].to<JsonArray>();
  xyz.add(vib_xyz[0]);
  xyz.add(vib_xyz[1]);
  xyz.add(vib_xyz[2]);
  doc["spl_ref_db"] = spl_ref_db;
  doc["spl_err_db"] = spl_err_db;
  doc["pwm_hz"] = static_cast<int>(pwm_hz);
  doc["pwm_mode"] = pwm_mode;
  doc["anc_on"] = anc_on;
  doc["vib_cancel_on"] = vib_cancel_on;
  doc["vib_cancel_effort"] = vib_cancel_effort;
  serializeJson(doc, *g_stream);
  *g_stream << "\n";
}

void Protocol::sendSpectrum(uint32_t ts,
                          const String& mic,
                          float fmax,
                          const float* bins,
                          size_t bin_count) {
  if (g_stream == nullptr) return;
  DynamicJsonDocument doc(4096);
  doc["t"] = "spec";
  doc["ts"] = ts;
  doc["vid"] = VEHICLE_ID;
  doc["mic"] = mic;
  doc["fmax"] = fmax;
  JsonArray arr = doc["bins"].to<JsonArray>();
  for (size_t i = 0; i < bin_count; ++i) {
    arr.add(bins[i]);
  }
  serializeJson(doc, *g_stream);
  *g_stream << "\n";
}

void Protocol::sendInference(const String& fault,
                           const float* conf,
                           float severity_raw,
                           const uint16_t* protected_hz,
                           size_t protected_count) {
  if (g_stream == nullptr) return;
  DynamicJsonDocument doc(512);
  doc["t"] = "inf";
  doc["ts"] = millis();
  doc["vid"] = VEHICLE_ID;
  doc["fault"] = fault;
  JsonObject conf_obj = doc.createNestedObject("conf");
  conf_obj["audio"] = conf[0];
  conf_obj["vib"] = conf[1];
  conf_obj["current"] = conf[2];
  doc["severity_raw"] = severity_raw;
  JsonArray prot = doc["protected_hz"].to<JsonArray>();
  for (size_t i = 0; i < protected_count; ++i) {
    prot.add(protected_hz[i]);
  }
  serializeJson(doc, *g_stream);
  *g_stream << "\n";
}

void Protocol::sendFeature(float rpm,
                          const float* audio_bands,
                          size_t audio_band_count,
                          const float* vib_bands,
                          size_t vib_band_count,
                          float vib_1x,
                          float vib_2x,
                          float i_mean,
                          float i_ripple,
                          float temp_c) {
  if (g_stream == nullptr) return;
  DynamicJsonDocument doc(2048);
  doc["t"] = "feat";
  doc["ts"] = millis();
  doc["vid"] = VEHICLE_ID;
  doc["rpm"] = rpm;
  JsonArray audio = doc["audio_bands"].to<JsonArray>();
  for (size_t i = 0; i < audio_band_count; ++i) {
    audio.add(audio_bands[i]);
  }
  JsonArray vib = doc["vib_bands"].to<JsonArray>();
  for (size_t i = 0; i < vib_band_count; ++i) {
    vib.add(vib_bands[i]);
  }
  doc["vib_1x"] = vib_1x;
  doc["vib_2x"] = vib_2x;
  doc["i_mean"] = i_mean;
  doc["i_ripple"] = i_ripple;
  doc["temp_c"] = temp_c;
  serializeJson(doc, *g_stream);
  *g_stream << "\n";
}

}  // namespace quietdrive
