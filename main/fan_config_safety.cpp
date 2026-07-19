#include "fan_config_safety.h"

#include <math.h>
#include <stdio.h>

#include "boards/board.h"
#include "esp_log.h"
#include "nvs_config.h"

namespace FanConfigSafety {

static const char *TAG = "fan_config";

static bool reject(char *error, size_t errorSize, const char *message)
{
    if (error && errorSize > 0) {
        snprintf(error, errorSize, "%s", message);
    }
    return false;
}

Settings load(Board *board, int channel)
{
    Settings settings = failSafe(channel);
    if (!board || channel < 0 || channel >= MAX_CHANNELS) {
        return settings;
    }

    PidSettings *defaults = board->getPidSettings(channel);
    settings.mode = Config::getFanMode(channel);
    settings.manualSpeed = Config::getFanManualSpeed(channel);
    settings.overheatTemp = Config::getFanOverheatTemp(channel);
    settings.targetTemp = Config::getFanPidTargetTemp(channel, defaults ? defaults->targetTemp : settings.targetTemp);
    settings.pidP = Config::getFanPidP(channel, defaults ? defaults->p : settings.pidP);
    settings.pidI = Config::getFanPidI(channel, defaults ? defaults->i : settings.pidI);
    settings.pidD = Config::getFanPidD(channel, defaults ? defaults->d : settings.pidD);
    return settings;
}

Settings failSafe(int channel)
{
    Settings settings;
    settings.mode = MODE_MANUAL;
    settings.manualSpeed = 100;
    settings.overheatTemp = channel == 1 ? 80 : 70;
    settings.targetTemp = channel == 1 ? 65 : 55;
    settings.pidP = 600;
    settings.pidI = 10;
    settings.pidD = 1000;
    return settings;
}

void store(int channel, const Settings &settings)
{
    if (channel < 0 || channel >= MAX_CHANNELS) {
        return;
    }
    Config::setFanMode(channel, settings.mode);
    Config::setFanManualSpeed(channel, settings.manualSpeed);
    Config::setFanOverheatTemp(channel, settings.overheatTemp);
    Config::setFanPidTargetTemp(channel, settings.targetTemp);
    Config::setFanPidP(channel, settings.pidP);
    Config::setFanPidI(channel, settings.pidI);
    Config::setFanPidD(channel, settings.pidD);
}

bool validate(const Settings &settings, int channel, int numFans,
              char *error, size_t errorSize)
{
    if (channel < 0 || channel >= numFans || channel >= MAX_CHANNELS) {
        return reject(error, errorSize, "fan channel is out of range");
    }
    if (settings.mode != MODE_MANUAL && settings.mode != MODE_PID &&
        !(settings.mode == MODE_LINKED && channel > 0)) {
        return reject(error, errorSize, "unsupported fan mode for channel");
    }
    if (settings.manualSpeed < MIN_MANUAL_SPEED || settings.manualSpeed > MAX_MANUAL_SPEED) {
        return reject(error, errorSize, "manual fan speed must be between 30 and 100 percent");
    }
    if (settings.overheatTemp < MIN_OVERHEAT_TEMP || settings.overheatTemp > MAX_OVERHEAT_TEMP) {
        return reject(error, errorSize, "fan overheat temperature must be between 40 and 90 C");
    }
    if (settings.targetTemp < MIN_TARGET_TEMP || settings.targetTemp > MAX_TARGET_TEMP) {
        return reject(error, errorSize, "fan PID target must be between 30 and 80 C");
    }
    if (settings.targetTemp + MIN_TEMP_MARGIN > settings.overheatTemp) {
        return reject(error, errorSize, "fan PID target must stay at least 5 C below overheat");
    }
    if (settings.pidP < 1 || settings.pidP > 10000) {
        return reject(error, errorSize, "fan PID P must be between 0.01 and 100");
    }
    if (settings.pidI > 1000) {
        return reject(error, errorSize, "fan PID I must be between 0 and 10");
    }
    if (settings.pidD > 10000) {
        return reject(error, errorSize, "fan PID D must be between 0 and 100");
    }
    return true;
}

bool encodePidValue(float value, PidTerm term, uint16_t *encoded)
{
    if (!encoded || !isfinite(value)) {
        return false;
    }

    float minimum = 0.0f;
    float maximum = 0.0f;
    switch (term) {
    case PidTerm::P:
        minimum = 0.01f;
        maximum = 100.0f;
        break;
    case PidTerm::I:
        maximum = 10.0f;
        break;
    case PidTerm::D:
        maximum = 100.0f;
        break;
    }
    if (value < minimum || value > maximum) {
        return false;
    }
    *encoded = static_cast<uint16_t>(lroundf(value * 100.0f));
    return true;
}

bool sanitizePersisted(Board *board, int channel, int numFans, Settings *settings)
{
    if (!settings) {
        return false;
    }
    *settings = load(board, channel);
    char error[128]{};
    if (validate(*settings, channel, numFans, error, sizeof(error))) {
        return false;
    }

    ESP_LOGE(TAG, "Unsafe persisted fan config on channel %d (%s); forcing 100%% manual", channel, error);
    *settings = failSafe(channel);
    store(channel, *settings);
    return true;
}

} // namespace FanConfigSafety
