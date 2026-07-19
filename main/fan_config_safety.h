#pragma once

#include <stddef.h>
#include <stdint.h>

class Board;

namespace FanConfigSafety {

constexpr int MAX_CHANNELS = 2;
constexpr uint16_t MIN_MANUAL_SPEED = 30;
constexpr uint16_t MAX_MANUAL_SPEED = 100;
constexpr uint16_t MIN_OVERHEAT_TEMP = 40;
constexpr uint16_t MAX_OVERHEAT_TEMP = 90;
constexpr uint16_t MIN_TARGET_TEMP = 30;
constexpr uint16_t MAX_TARGET_TEMP = 80;
constexpr uint16_t MIN_TEMP_MARGIN = 5;

constexpr uint16_t MODE_MANUAL = 0;
constexpr uint16_t MODE_PID = 2;
constexpr uint16_t MODE_LINKED = 3;

struct Settings {
    uint16_t mode = MODE_MANUAL;
    uint16_t manualSpeed = 100;
    uint16_t overheatTemp = 70;
    uint16_t targetTemp = 55;
    uint16_t pidP = 600;
    uint16_t pidI = 10;
    uint16_t pidD = 1000;
};

enum class PidTerm {
    P,
    I,
    D,
};

Settings load(Board *board, int channel);
Settings failSafe(int channel);
void store(int channel, const Settings &settings);

bool validate(const Settings &settings, int channel, int numFans,
              char *error, size_t errorSize);
bool encodePidValue(float value, PidTerm term, uint16_t *encoded);

// Returns true when an invalid persisted config was replaced with a
// deterministic full-speed fail-safe config.
bool sanitizePersisted(Board *board, int channel, int numFans, Settings *settings);

} // namespace FanConfigSafety
