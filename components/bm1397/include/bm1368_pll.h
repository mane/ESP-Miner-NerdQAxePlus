#pragma once

#include <stddef.h>
#include <stdint.h>

namespace BM1368Pll {

struct LowVcoPreset {
    uint16_t nominalMhz;
    float actualMhz;
    uint8_t feedbackDivider;
};

// Explicit low-VCO BM1368 points for register 0x08. They continue the
// proven 525MHz/A8 and 540MHz/AD sequence without entering the high-VCO mode:
//   531MHz nominal -> 531.250MHz physical, feedback divider 0xAA
//   534MHz nominal -> 534.375MHz physical, feedback divider 0xAB
//   537MHz nominal -> 537.500MHz physical, feedback divider 0xAC
//   540MHz nominal -> 540.625MHz physical, feedback divider 0xAD
static constexpr LowVcoPreset LOW_VCO_PRESETS[] = {
    {531, 531.250f, 0xAA},
    {534, 534.375f, 0xAB},
    {537, 537.500f, 0xAC},
    {540, 540.625f, 0xAD},
};

static constexpr float TARGET_EPSILON_MHZ = 0.001f;

inline bool findLowVcoPreset(float targetMhz, LowVcoPreset *preset)
{
    if (!preset) {
        return false;
    }

    for (size_t i = 0; i < sizeof(LOW_VCO_PRESETS) / sizeof(LOW_VCO_PRESETS[0]); ++i) {
        const float delta = targetMhz - static_cast<float>(LOW_VCO_PRESETS[i].nominalMhz);
        const float magnitude = delta < 0.0f ? -delta : delta;
        if (magnitude <= TARGET_EPSILON_MHZ) {
            *preset = LOW_VCO_PRESETS[i];
            return true;
        }
    }
    return false;
}

inline void encodeLowVcoPayload(const LowVcoPreset &preset, uint8_t payload[6])
{
    payload[0] = 0x00;
    payload[1] = 0x08;
    payload[2] = 0x40;
    payload[3] = preset.feedbackDivider;
    payload[4] = 0x02;
    payload[5] = 0x30;
}

} // namespace BM1368Pll
