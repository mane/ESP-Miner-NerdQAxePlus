#pragma once

#include <stddef.h>

#include "ArduinoJson.h"
#include "fan_config_safety.h"

class Board;

struct FanSettingsPatch {
    FanConfigSafety::Settings channels[FanConfigSafety::MAX_CHANNELS];
    bool touched[FanConfigSafety::MAX_CHANNELS]{};
    int numFans = 0;
};

// Parses and validates every fan field before the caller mutates any Config
// value. legacyTopLevel enables the old ch0 aliases used by /api/system.
bool parseFanSettingsPatch(JsonDocument &doc, Board *board, bool legacyTopLevel,
                           FanSettingsPatch *patch, char *error, size_t errorSize);

void applyFanSettingsPatch(const FanSettingsPatch &patch);
