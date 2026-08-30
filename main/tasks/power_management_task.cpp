#include <algorithm>
#include <math.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "mining.h"
#include "periodic.hpp"

#include "boards/board.h"
#include "fan_controller.h"
#include "global_state.h"
#include "influx_task.h"
#include "nvs_config.h"
#include "serial.h"
#include "asic_result_task.h"

#define POLL_RATE 2000

static const char *TAG = "power_management";
static constexpr uint32_t HASHRATE_SAMPLE_MAX_AGE_MS = 12000;
static constexpr uint16_t GOVERNOR_LOW_VOLTAGE_CAP_MHZ = 500;
static constexpr uint16_t GOVERNOR_HIGH_FREQUENCY_MIN_MV = 1300;

static bool sameGovernorLimits(const HashrateGovernor::Limits &left,
                               const HashrateGovernor::Limits &right)
{
    return left.powerLimitWatts == right.powerLimitWatts &&
           left.currentLimitAmps == right.currentLimitAmps &&
           left.chipTemperatureLimitC == right.chipTemperatureLimitC &&
           left.vrTemperatureLimitC == right.vrTemperatureLimitC &&
           left.expectedChips == right.expectedChips &&
           left.maxTelemetryAgeMs == right.maxTelemetryAgeMs;
}

// #define MEASURE_LOOP_TIME

PowerManagementTask::PowerManagementTask()
{
    m_mutex = xSemaphoreCreateRecursiveMutex();
}

void PowerManagementTask::taskWrapper(void *pvParameters)
{
    PowerManagementTask *powerManagementTask = (PowerManagementTask *) pvParameters;
    powerManagementTask->task();
}

void PowerManagementTask::restart()
{
    ESP_LOGW(TAG, "Shutdown requested ...");
    // stops the main task
    lock();

    ESP_LOGW(TAG, "HW lock acquired!");
    // shutdown asics and LDOs before reset
    shutdown();

    ESP_LOGW(TAG, "restart");
    esp_restart();

    // unreachable
    unlock();
}

void PowerManagementTask::shutdown()
{
    lock();
    if (m_board) {
        m_shutdown = true;
        m_board->shutdown();
    }
    unlock();
}

uint16_t PowerManagementTask::getFanRPM(int channel)
{
    return m_fanController.getRPM(channel);
}

const char *PowerManagementTask::governorStateString() const
{
    switch (m_governorState) {
        case HashrateGovernor::State::DISABLED: return "disabled";
        case HashrateGovernor::State::WARMUP: return "warmup";
        case HashrateGovernor::State::OBSERVE: return "observe";
        case HashrateGovernor::State::PROBE: return "probe";
        case HashrateGovernor::State::COOLDOWN: return "cooldown";
    }
    return "unknown";
}

void PowerManagementTask::copyHashrateGovernorStatus(HashrateGovernorStatus *status)
{
    if (!status) return;

    lock();
    status->enabled = m_governorEnabled;
    status->targetFrequency = m_runtimeFrequencyTarget;
    status->lastStableFrequency = m_hashrateGovernor.stableRuntimeMhz();
    status->utilization = m_governorUtilization;
    status->state = governorStateString();
    status->lastReason = HashrateGovernor::Governor::reasonString(m_governorReason);
    unlock();
}

void PowerManagementTask::checkVrFrequencyChanged()
{
    static uint32_t lastVrFrequency = 0;

    uint32_t vrFrequency = m_board->getVrFrequency();
    if (vrFrequency != lastVrFrequency) {
        if (m_board->setVrFrequency(vrFrequency)) {
            ESP_LOGI(TAG, "setting version rolling frequency to %luHz", vrFrequency);
            lastVrFrequency = vrFrequency;
        } else {
            ESP_LOGE(TAG, "failed to set version rolling frequency to %luHz; will retry", vrFrequency);
        }
    }
}


void PowerManagementTask::logChipTemps()
{
    size_t offset = 0;

    // no chip temp to report
    if (m_board->getMaxChipTemp() == 0.0f) {
        return;
    }

    // Iterate through each ASIC and append its count to the log message
    for (int i = 0; i < m_board->getAsicCount(); i++) {
        size_t remaining = sizeof(m_logBuffer) - offset;
        int written = snprintf(m_logBuffer + offset, remaining, "%.2f°C / ", m_board->getChipTemp(i));
        if (written < 0) {
            return;
        }
        if (static_cast<size_t>(written) >= remaining) {
            offset = sizeof(m_logBuffer) - 1;
            break;
        }
        offset += static_cast<size_t>(written);
    }
    if (offset >= 2) {
        m_logBuffer[offset - 2] = 0; // remove trailing slash
    }

    ESP_LOGI(TAG, "chip temperatures: %s", m_logBuffer);
}

void PowerManagementTask::create_job_timer(TimerHandle_t xTimer)
{
    // Retrieve 'this' pointer from timer ID
    PowerManagementTask *task = (PowerManagementTask *) pvTimerGetTimerID(xTimer);
    if (!task) {
        return;
    }
    task->trigger();
}

void PowerManagementTask::trigger()
{
    pthread_mutex_lock(&m_loop_mutex);
    m_loopPending = true;
    pthread_cond_signal(&m_loop_cond);
    pthread_mutex_unlock(&m_loop_mutex);
}

bool PowerManagementTask::startTimer()
{
    // Create the timer
    m_timer = xTimerCreate(TAG, pdMS_TO_TICKS(POLL_RATE), pdTRUE, (void *) this, create_job_timer);

    if (m_timer == NULL) {
        ESP_LOGE(TAG, "Failed to create timer");
        return false;
    }

    // Start the timer
    if (xTimerStart(m_timer, 0) != pdPASS) {
        ESP_LOGE(TAG, "Failed to start timer");
        return false;
    }
    return true;
}

void PowerManagementTask::readAndPublishPowerTelemetry()
{
    if (!m_board->isBuckInitialized()) {
        return;
    }

    static Periodic every_15s(sec_to_us(15), /*start_immediately=*/true);

    // request buck telemetry
    if (every_15s.due()) {
        m_board->requestBuckTelemtry();
    }

    float vin = m_board->getVin();
    float iin = m_board->getIin();
    float pin = m_board->getPin();
    float pout = m_board->getPout();
    float vout = m_board->getVout();
    float iout = m_board->getIout();

    m_vrTemp = m_board->getVRTemp();
    m_vrTempInt = m_board->getVRTempInt();

    ESP_LOGI(TAG, "vin: %.2f, iin: %.2f, pin: %.2f, vout: %.2f, iout: %.2f, pout: %.2f, vr-temp: %.2f, vr-temp-int: %.2f", vin, iin,
             pin, vout, iout, pout, m_vrTemp, m_vrTempInt);

    influx_task_set_pwr(vin, iin, pin, vout, iout, pout);

    // currently only implemented for boards with TPS536x7
    uint32_t status = 0;
    Board::Error error = m_board->getFault(&status);
    if (error != Board::Error::NONE) {
        SYSTEM_MODULE.setBoardError(error, status);
        m_board->setVoltage(0.0);
    }

    m_voltage = vin * 1000.0;
    m_current = iin * 1000.0;
    m_power = pin;
    if (isfinite(vin) && vin > 0.0f && isfinite(iin) && iin >= 0.0f &&
        isfinite(pin) && pin > 0.0f && isfinite(m_vrTemp) && m_vrTemp > 0.0f) {
        m_lastTelemetryMs = (uint64_t) (esp_timer_get_time() / 1000ULL);
    }
}

void PowerManagementTask::syncHashrateGovernorConfiguration(
    uint64_t nowMs, const HashrateGovernor::Sample &sample)
{
    const uint16_t baseFrequency = (uint16_t) m_board->getAsicFrequency();
    const uint16_t baseVoltageMillis = (uint16_t) m_board->getAsicVoltageMillis();
    uint16_t maxFrequency = Config::getHashrateGovernorMaxFrequency();
    uint16_t powerLimit10 = Config::getHashrateGovernorPowerLimit10();
    const bool enabled = Config::isHashrateGovernorEnabled();

    if (!m_board->isSupportedAsicFrequency(maxFrequency) || maxFrequency > 550) {
        maxFrequency = baseFrequency;
    }
    if (maxFrequency < baseFrequency) maxFrequency = baseFrequency;
    bool voltageLimited = false;
    if (baseVoltageMillis < GOVERNOR_HIGH_FREQUENCY_MIN_MV &&
        maxFrequency > GOVERNOR_LOW_VOLTAGE_CAP_MHZ) {
        // Do not raise Vcore implicitly. At low persistent voltages, cap an
        // automatic probe at the highest qualified point <=500MHz. If the
        // manually selected base is already higher, merely prevent ascent.
        uint16_t voltageSafeMax = baseFrequency;
        for (uint32_t option : m_board->getFrequencyOptions()) {
            if (option >= baseFrequency && option <= GOVERNOR_LOW_VOLTAGE_CAP_MHZ &&
                option > voltageSafeMax) {
                voltageSafeMax = (uint16_t) option;
            }
        }
        voltageLimited = voltageSafeMax < maxFrequency;
        maxFrequency = voltageSafeMax;
    }
    powerLimit10 = std::max<uint16_t>(300, std::min<uint16_t>(powerLimit10, 690));

    HashrateGovernor::Limits limits;
    limits.powerLimitWatts = (double) powerLimit10 / 10.0;
    limits.currentLimitAmps = std::max(0.1f, std::min(5.9f, m_board->getMaxCurrentA() - 0.1f));
    limits.chipTemperatureLimitC = std::min(65.0, std::max(40.0, (double) Config::getFanOverheatTemp(0) - 5.0));
    limits.vrTemperatureLimitC = std::min(75.0, std::max(40.0, (double) Config::getFanOverheatTemp(1) - 5.0));
    limits.expectedChips = (uint16_t) m_board->getAsicCount();
    limits.maxTelemetryAgeMs = HASHRATE_SAMPLE_MAX_AGE_MS;

    // This runs every two seconds. Keep the unchanged path allocation-free,
    // while including every safety-relevant field that can change at runtime.
    // The board's qualified frequency table is immutable after initialization.
    if (m_governorConfigured && baseFrequency == m_governorBaseFrequency &&
        baseVoltageMillis == m_governorBaseVoltageMillis &&
        maxFrequency == m_governorMaxFrequency &&
        powerLimit10 == m_governorPowerLimit10 && enabled == m_governorEnabled &&
        sameGovernorLimits(limits, m_governorLimits)) {
        return;
    }

    std::vector<uint16_t> governorFrequencies;
    governorFrequencies.reserve(m_board->getFrequencyOptions().size());
    for (uint32_t frequency : m_board->getFrequencyOptions()) {
        if (frequency > 0 && frequency <= UINT16_MAX) {
            governorFrequencies.push_back((uint16_t) frequency);
        }
    }

    if (m_governorConfigured && enabled == m_governorEnabled &&
        baseVoltageMillis == m_governorBaseVoltageMillis &&
        m_hashrateGovernor.configurationMatches(
            governorFrequencies, baseFrequency, limits, maxFrequency)) {
        return;
    }

    if (voltageLimited) {
        ESP_LOGW(TAG, "governor max limited to %uMHz: %umV Vcore is below the 1300mV high-frequency floor",
                 maxFrequency, (unsigned int) baseVoltageMillis);
    }

    const uint16_t previousRuntimeTarget = m_hashrateGovernor.runtimeTargetMhz();
    const bool capOnlyCandidate =
        m_governorConfigured && enabled && m_governorEnabled &&
        baseFrequency == m_governorBaseFrequency &&
        baseVoltageMillis == m_governorBaseVoltageMillis &&
        maxFrequency != m_governorMaxFrequency;
    const bool preserved = capOnlyCandidate &&
        m_hashrateGovernor.reconfigureCapPreservingStable(
            governorFrequencies, baseFrequency, limits, maxFrequency, sample);

    if (!preserved) {
        m_governorConfigured = m_hashrateGovernor.configure(
            governorFrequencies, baseFrequency, limits, maxFrequency);
        if (!m_governorConfigured) {
            ESP_LOGE(TAG, "failed to configure hashrate governor; using persistent base %uMHz", baseFrequency);
            m_governorEnabled = false;
            m_runtimeFrequencyTarget = baseFrequency;
            m_governorState = HashrateGovernor::State::DISABLED;
            m_governorReason = HashrateGovernor::Reason::INVALID_SAMPLE;
            return;
        }
        m_hashrateGovernor.setEnabled(enabled, nowMs);
    }

    m_governorBaseFrequency = baseFrequency;
    m_governorBaseVoltageMillis = baseVoltageMillis;
    m_governorMaxFrequency = maxFrequency;
    m_governorPowerLimit10 = powerLimit10;
    m_governorLimits = limits;
    m_governorEnabled = enabled;
    m_runtimeFrequencyTarget = m_hashrateGovernor.runtimeTargetMhz();
    m_governorState = m_hashrateGovernor.state();
    m_governorReason = !enabled
        ? HashrateGovernor::Reason::DISABLED
        : (m_governorState == HashrateGovernor::State::OBSERVE
            ? HashrateGovernor::Reason::OBSERVING
            : HashrateGovernor::Reason::WARMING_UP);

    // A reduced cap (including an aborted in-flight probe) must not spend
    // several normal PLL increments above the newly allowed target. Keep the
    // wider rollback step latched until the physical clock reaches the target.
    m_governorEmergency = sample.frequencyMhz > (double) m_runtimeFrequencyTarget + 0.01;

    if (preserved) {
        ESP_LOGI(TAG,
                 "hashrate governor cap updated without base reset: old-target=%uMHz stable=%uMHz max=%uMHz",
                 previousRuntimeTarget, m_hashrateGovernor.stableRuntimeMhz(), maxFrequency);
    } else {
        ESP_LOGI(TAG, "hashrate governor %s: base=%uMHz max=%uMHz power=%.1fW",
                 enabled ? "enabled" : "disabled", baseFrequency, maxFrequency,
                 (double) powerLimit10 / 10.0);
    }
}

void PowerManagementTask::updateHashrateGovernor(uint64_t nowMs)
{
    HashrateGovernor::Sample sample;
    sample.nowMs = nowMs;
    sample.frequencyMhz = m_board->getEffectiveAsicFrequency();
    float freshHashrateGhs = 0.0f;
    const bool hasFreshHashrate = HASHRATE_MONITOR.getFreshSmoothedTotalChipHashrate(
        nowMs, HASHRATE_SAMPLE_MAX_AGE_MS, &freshHashrateGhs);
    sample.hashrateGhs = freshHashrateGhs;
    sample.powerWatts = m_power;
    sample.currentAmps = m_current / 1000.0f;
    sample.chipTemperatureC = m_chipTempMax;
    // On this board the TPS internal sensor is consistently about 10C above
    // the external VR sensor. Normalize that documented offset, but retain the
    // hotter of the two readings so neither sensor is ignored.
    sample.vrTemperatureC = std::max(
        m_vrTemp, m_vrTempInt > 10.0f ? m_vrTempInt - 10.0f : m_vrTempInt);
    sample.detectedChips = (uint16_t) m_board->getDetectedAsicCount();
    // M2/fan0 is the ASIC fan and has a tachometer. M1/fan1 on NerdQAxe+
    // commonly reports 0 RPM even at 100%, so it cannot be a mandatory veto.
    sample.fanHealthy = m_board->getNumFans() > 0 &&
                        m_fanController.getSpeedPerc(0) >= 30 &&
                        m_fanController.getRPM(0) > 0;
    sample.poolReady = STRATUM_MANAGER && STRATUM_MANAGER->isAnyConnected();
    sample.telemetryValid = m_lastTelemetryMs > 0 && m_power > 0.0f && m_voltage > 0.0f &&
                            m_chipTempMax > 0.0f && sample.vrTemperatureC > 0.0 &&
                            hasFreshHashrate;
    sample.telemetryAgeMs = m_lastTelemetryMs > 0 && nowMs >= m_lastTelemetryMs
        ? nowMs - m_lastTelemetryMs
        : UINT64_MAX;
    sample.rejectedShares = STRATUM_MANAGER ? STRATUM_MANAGER->getSharesRejectedSnapshot() : 0;
    sample.duplicateShares = getDuplicateHWNonces();

    syncHashrateGovernorConfiguration(nowMs, sample);
    if (!m_governorConfigured) return;

    const HashrateGovernor::State oldState = m_governorState;
    const HashrateGovernor::Reason oldReason = m_governorReason;
    const uint16_t oldTarget = m_runtimeFrequencyTarget;
    const HashrateGovernor::Decision decision = m_hashrateGovernor.update(sample);

    m_runtimeFrequencyTarget = decision.targetFrequencyMhz
        ? decision.targetFrequencyMhz
        : m_governorBaseFrequency;
    if (decision.emergency) {
        m_governorEmergency = true;
    } else if (fabs(sample.frequencyMhz - (double) m_runtimeFrequencyTarget) <= 0.01) {
        // Keep the wider emergency PLL step latched until the physical clock
        // reaches the rollback target; COOLDOWN decisions themselves are holds.
        m_governorEmergency = false;
    }
    m_governorReason = decision.reason;
    m_governorState = m_hashrateGovernor.state();
    m_governorUtilization = (float) HashrateGovernor::Governor::hashrateRatio(sample);

    if (oldState != m_governorState || oldReason != m_governorReason || oldTarget != m_runtimeFrequencyTarget) {
        ESP_LOGI(TAG, "governor state=%s reason=%s target=%uMHz utilization=%.4f",
                 governorStateString(), HashrateGovernor::Governor::reasonString(m_governorReason),
                 m_runtimeFrequencyTarget, m_governorUtilization);
    }
}

void PowerManagementTask::applyRuntimeAsicSettings()
{
    if (m_shutdown || !m_board->isInitialized()) return;

    checkVrFrequencyChanged();

    const uint16_t targetFrequency = m_runtimeFrequencyTarget
        ? m_runtimeFrequencyTarget
        : (uint16_t) m_board->getAsicFrequency();
    const uint16_t targetVoltage = (uint16_t) m_board->getAsicVoltageMillis();
    const float currentFrequency = m_board->getEffectiveAsicFrequency();

    if (!m_appliedCoreVoltageMillis) {
        const float actualVoltage = m_board->getVout();
        m_appliedCoreVoltageMillis = isfinite(actualVoltage) && actualVoltage > 0.0f
            ? (uint16_t) lroundf(actualVoltage * 1000.0f)
            : targetVoltage;
    }

    // Upclock: raise V first when needed. Downclock: lower F first and defer a
    // voltage reduction until a later control tick. Each PLL tick emits at
    // most one command and never sleeps while the hardware lock is held.
    if (currentFrequency < (float) targetFrequency - 0.01f) {
        if (targetVoltage > m_appliedCoreVoltageMillis) {
            ESP_LOGI(TAG, "raising vcore to %umV before frequency increase", targetVoltage);
            if (m_board->setVoltage((float) targetVoltage / 1000.0f)) {
                m_appliedCoreVoltageMillis = targetVoltage;
            }
            return;
        }
        const float maxStep = m_governorEmergency ? 25.0f : 6.25f;
        if (!m_board->stepAsicFrequency((float) targetFrequency, maxStep)) {
            ESP_LOGE(TAG, "failed runtime PLL step toward %uMHz", targetFrequency);
        }
        return;
    }

    if (currentFrequency > (float) targetFrequency + 0.01f) {
        const float maxStep = m_governorEmergency ? 25.0f : 6.25f;
        if (!m_board->stepAsicFrequency((float) targetFrequency, maxStep)) {
            ESP_LOGE(TAG, "failed runtime PLL rollback toward %uMHz", targetFrequency);
        }
        return;
    }

    if (targetVoltage != m_appliedCoreVoltageMillis) {
        ESP_LOGI(TAG, "setting vcore to %umV after PLL settled", targetVoltage);
        if (m_board->setVoltage((float) targetVoltage / 1000.0f)) {
            m_appliedCoreVoltageMillis = targetVoltage;
        } else {
            ESP_LOGE(TAG, "failed to set vcore to %umV; will retry", targetVoltage);
        }
    }

}

void PowerManagementTask::requestChipTemps()
{
    // temperature measurements don't work before ASICs
    // are initialized
    if (!m_board->isInitialized()) {
        return;
    }

    m_board->requestChipTemps();
}

void PowerManagementTask::task()
{
    m_board = SYSTEM_MODULE.getBoard();

    // use manual invert polarity setting
    bool invert = m_board->isInvertFanPolarityEnabled();

    m_board->setFanPolarity(invert);

    m_fanController.init(m_board, POLL_RATE);

    vTaskDelay(pdMS_TO_TICKS(1000));
    startTimer();

    uint64_t last_time = esp_timer_get_time();
    while (1) {
        pthread_mutex_lock(&m_loop_mutex);
        while (!m_loopPending) {
            pthread_cond_wait(&m_loop_cond, &m_loop_mutex);
        }
        m_loopPending = false;
        pthread_mutex_unlock(&m_loop_mutex);

        uint64_t start = esp_timer_get_time();
        lock();

        // request chip temps
        requestChipTemps();

        logChipTemps();

        readAndPublishPowerTelemetry();

        // collect temperatures
        // get the max of all asic measuring temp sensors
        float tmp1075Max = 0.0f;
        for (int i = 0; i < m_board->getNumTempSensors(); i++) {
            float tmp = m_board->getTemperature(i);
            if (tmp) {
                ESP_LOGI(TAG, "Temperature %d: %.2f C", i, tmp);
            }
            tmp1075Max = std::max(tmp1075Max, tmp);
        }

        // get max temp of all chips
        // returns 0 if not available on the hardware
        float intChipTempMax = m_board->getMaxChipTemp();

#ifdef NERDQAXEPLUS
        // NQ+ needs special care - the reading of chip internal temp sensors is way
        // too slow for the PID, so we need to stay compatible.
        // we use the max temp of board temp sensors and ASICs
        // note: m_chipTempMax is not mutexed, single assignment required
        m_chipTempMax = std::max(tmp1075Max, intChipTempMax);
#else
        // on other devices that have the TMUX like the QX we only use
        // the chip temps for the PID
        // note: m_chipTempMax is not mutexed, single assignment required
        m_chipTempMax = intChipTempMax ? intChipTempMax : tmp1075Max;
#endif

        influx_task_set_temperature(m_chipTempMax, m_vrTemp);

        // Run fan controller (reads RPM, drives fans, updates overheat flags)
        m_fanController.update(m_chipTempMax, m_vrTemp);

        // Shutdown if any fan channel reports overheat
        if (m_fanController.isOverheated(0) || m_fanController.isOverheated(1)) {
            uint32_t status = ((uint32_t) m_chipTempMax << 24) | ((uint32_t) m_fanController.getOverheatTemp(0) << 16) |
                              ((uint32_t) m_vrTemp << 8) | ((uint32_t) m_fanController.getOverheatTemp(1));

            // over temperature — ASIC takes priority over VReg-only
            Board::Error overheatErr = Board::Error::VREG_TEMP_FAULT;
            if (m_fanController.isOverheated(0)) overheatErr = Board::Error::TEMP_FAULT;
            SYSTEM_MODULE.setBoardError(overheatErr, status);

            // disables the buck
            m_board->setVoltage(0.0);
            ESP_LOGE(TAG, "System overheated (chip=%.1f°C/thresh=%d°C vr=%.2f°C/thresh=%d°C) - Shutting down asic voltage",
                     m_chipTempMax, m_fanController.getOverheatTemp(0), m_vrTemp, m_fanController.getOverheatTemp(1));
        }
        influx_set_fan(m_fanController.getSpeedPerc(0), (float) m_fanController.getRPM(0), m_fanController.getSpeedPerc(1),
                       (float) m_fanController.getRPM(1));

        const uint64_t nowMs = (uint64_t) (esp_timer_get_time() / 1000ULL);
        updateHashrateGovernor(nowMs);
        if (SYSTEM_MODULE.getBoardError() == Board::Error::NONE) {
            applyRuntimeAsicSettings();
        } else {
            // A fault handler may have disabled the buck. Forget the cached
            // applied voltage so it cannot be mistaken for a live rail.
            m_appliedCoreVoltageMillis = 0;
        }
        unlock();
#ifdef MEASURE_LOOP_TIME
        // checks if loop takes too much time
        uint64_t end = esp_timer_get_time();
        uint64_t duration = (end - start) / 1000llu;
        uint64_t interval = (start - last_time) / 1000llu;
        if (duration > POLL_RATE) {
            ESP_LOGE(TAG, "loop taking more then %dms (%llums, interval: %llu)", POLL_RATE, duration, interval);
        }
        last_time = start;
#endif
    }
}
