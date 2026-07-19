#include "fan_settings_patch.h"

#include <algorithm>
#include <stdio.h>

#include "boards/board.h"

static bool rejectFanPatch(char *error, size_t errorSize, const char *message)
{
    if (error && errorSize > 0) {
        snprintf(error, errorSize, "%s", message);
    }
    return false;
}

static bool parseU16(JsonObject object, const char *key, uint16_t *destination,
                     bool *touched, char *error, size_t errorSize)
{
    if (!object.containsKey(key)) {
        return true;
    }
    if (!object[key].is<uint16_t>()) {
        return rejectFanPatch(error, errorSize, "fan setting must be an unsigned 16-bit integer");
    }
    *destination = object[key].as<uint16_t>();
    *touched = true;
    return true;
}

static bool parsePid(JsonObject object, FanConfigSafety::Settings *settings,
                     bool *touched, char *error, size_t errorSize)
{
    struct PidField {
        const char *key;
        FanConfigSafety::PidTerm term;
        uint16_t *destination;
    };
    PidField fields[] = {
        {"p", FanConfigSafety::PidTerm::P, &settings->pidP},
        {"i", FanConfigSafety::PidTerm::I, &settings->pidI},
        {"d", FanConfigSafety::PidTerm::D, &settings->pidD},
    };

    if (!parseU16(object, "targetTemp", &settings->targetTemp, touched, error, errorSize)) {
        return false;
    }
    for (const PidField &field : fields) {
        if (!object.containsKey(field.key)) {
            continue;
        }
        if (!object[field.key].is<float>()) {
            return rejectFanPatch(error, errorSize, "fan PID value must be a finite number");
        }
        if (!FanConfigSafety::encodePidValue(object[field.key].as<float>(), field.term, field.destination)) {
            return rejectFanPatch(error, errorSize, "fan PID value is outside the safe range");
        }
        *touched = true;
    }
    return true;
}

static bool parseFanObject(JsonObject fan, FanConfigSafety::Settings *settings,
                           bool *touched, char *error, size_t errorSize)
{
    if (!parseU16(fan, "mode", &settings->mode, touched, error, errorSize) ||
        !parseU16(fan, "manualSpeed", &settings->manualSpeed, touched, error, errorSize) ||
        !parseU16(fan, "overheatTemp", &settings->overheatTemp, touched, error, errorSize)) {
        return false;
    }
    if (fan.containsKey("pid")) {
        if (!fan["pid"].is<JsonObject>()) {
            return rejectFanPatch(error, errorSize, "fan pid must be an object");
        }
        if (!parsePid(fan["pid"].as<JsonObject>(), settings, touched, error, errorSize)) {
            return false;
        }
    }
    return true;
}

static bool parseLegacy(JsonObject root, FanConfigSafety::Settings *settings,
                        bool *touched, char *error, size_t errorSize)
{
    if (!parseU16(root, "overheat_temp", &settings->overheatTemp, touched, error, errorSize) ||
        !parseU16(root, "autofanspeed", &settings->mode, touched, error, errorSize) ||
        !parseU16(root, "manualFanSpeed", &settings->manualSpeed, touched, error, errorSize) ||
        !parseU16(root, "pidTargetTemp", &settings->targetTemp, touched, error, errorSize)) {
        return false;
    }

    struct LegacyPidField {
        const char *key;
        FanConfigSafety::PidTerm term;
        uint16_t *destination;
    };
    LegacyPidField fields[] = {
        {"pidP", FanConfigSafety::PidTerm::P, &settings->pidP},
        {"pidI", FanConfigSafety::PidTerm::I, &settings->pidI},
        {"pidD", FanConfigSafety::PidTerm::D, &settings->pidD},
    };
    for (const LegacyPidField &field : fields) {
        if (!root.containsKey(field.key)) {
            continue;
        }
        if (!root[field.key].is<float>() ||
            !FanConfigSafety::encodePidValue(root[field.key].as<float>(), field.term, field.destination)) {
            return rejectFanPatch(error, errorSize, "legacy fan PID value is outside the safe range");
        }
        *touched = true;
    }
    return true;
}

bool parseFanSettingsPatch(JsonDocument &doc, Board *board, bool legacyTopLevel,
                           FanSettingsPatch *patch, char *error, size_t errorSize)
{
    if (!board || !patch) {
        return rejectFanPatch(error, errorSize, "fan controller is unavailable");
    }

    JsonObject root = doc.as<JsonObject>();
    patch->numFans = std::min(board->getNumFans(), FanConfigSafety::MAX_CHANNELS);
    if (patch->numFans <= 0) {
        return rejectFanPatch(error, errorSize, "board has no controllable fans");
    }

    for (int channel = 0; channel < patch->numFans; ++channel) {
        patch->channels[channel] = FanConfigSafety::load(board, channel);
        char persistedError[128]{};
        if (!FanConfigSafety::validate(patch->channels[channel], channel, patch->numFans,
                                       persistedError, sizeof(persistedError))) {
            // Do not let an old unsafe value become the baseline for a partial
            // PATCH. FanController will also persist this repair on reload.
            patch->channels[channel] = FanConfigSafety::failSafe(channel);
        }
    }

    if (legacyTopLevel &&
        !parseLegacy(root, &patch->channels[0], &patch->touched[0], error, errorSize)) {
        return false;
    }

    if (root.containsKey("fans")) {
        if (!root["fans"].is<JsonArray>()) {
            return rejectFanPatch(error, errorSize, "fans must be an array");
        }
        JsonArray fans = root["fans"].as<JsonArray>();
        if (fans.size() > static_cast<size_t>(patch->numFans)) {
            return rejectFanPatch(error, errorSize, "fan array exceeds board channel count");
        }
        int channel = 0;
        for (JsonVariant entry : fans) {
            if (!entry.is<JsonObject>()) {
                return rejectFanPatch(error, errorSize, "fan entry must be an object");
            }
            if (!parseFanObject(entry.as<JsonObject>(), &patch->channels[channel],
                                &patch->touched[channel], error, errorSize)) {
                return false;
            }
            ++channel;
        }
    }

    for (int channel = 0; channel < patch->numFans; ++channel) {
        if (patch->touched[channel] &&
            !FanConfigSafety::validate(patch->channels[channel], channel, patch->numFans,
                                       error, errorSize)) {
            return false;
        }
    }
    return true;
}

void applyFanSettingsPatch(const FanSettingsPatch &patch)
{
    for (int channel = 0; channel < patch.numFans; ++channel) {
        if (patch.touched[channel]) {
            FanConfigSafety::store(channel, patch.channels[channel]);
        }
    }
}
