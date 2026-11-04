#pragma once

#include <Arduino.h>

namespace quietdrive {

// I2S microphone capture pins
constexpr uint8_t I2S_MIC_SCK = 12;
constexpr uint8_t I2S_MIC_WS = 13;
constexpr uint8_t I2S_MIC_SD = 14;

// I2S speaker output pins
constexpr uint8_t I2S_SPK_BCLK = 15;
constexpr uint8_t I2S_SPK_LRC = 16;
constexpr uint8_t I2S_SPK_DIN = 17;

// I2C vibration sensor
constexpr uint8_t ADXL345_SDA = 8;
constexpr uint8_t ADXL345_SCL = 9;
constexpr uint8_t ADXL345_ADDRESS = 0x53;

// Hall sensor and motor control
constexpr uint8_t HALL_SENSOR_PIN = 5;
constexpr uint8_t ACS712_PIN = 4;
constexpr uint8_t MOTOR_PIN = 7;
constexpr uint8_t BUZZER_PIN = 21;

// Temperature and RGB
constexpr uint8_t DS18B20_PIN = 6;
constexpr uint8_t RGB_R_PIN = 39;
constexpr uint8_t RGB_G_PIN = 40;
constexpr uint8_t RGB_B_PIN = 41;

// Onboard WS2812 strip
#ifndef ONBOARD_WS2812_PIN
#define ONBOARD_WS2812_PIN 48
#endif

}  // namespace quietdrive
