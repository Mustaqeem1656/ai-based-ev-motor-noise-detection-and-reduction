#include "temp.h"

#include <OneWire.h>
#include <DallasTemperature.h>

namespace quietdrive {

namespace {
OneWire g_one_wire(DS18B20_PIN);
DallasTemperature g_sensors(&g_one_wire);
}

TemperatureSensor::TemperatureSensor() = default;

void TemperatureSensor::begin() {
  g_sensors.begin();
  enabled_ = (g_sensors.getDeviceCount() > 0);
  if (enabled_) {
    g_sensors.setResolution(12);
  }
}

void TemperatureSensor::update() {
  if (!enabled_) return;
  g_sensors.requestTemperatures();
  temp_c_ = g_sensors.getTempCByIndex(0);
}

float TemperatureSensor::celsius() const {
  return temp_c_;
}

}  // namespace quietdrive
