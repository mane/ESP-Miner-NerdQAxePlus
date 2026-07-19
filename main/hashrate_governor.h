#pragma once

#include <stdint.h>

#include <vector>

namespace HashrateGovernor {

static constexpr uint16_t DEFAULT_LOGICAL_CAP_MHZ = 550;
static constexpr uint16_t ALWAYS_EXCLUDED_FREQUENCY_MHZ = 575;
static constexpr uint64_t WARMUP_MS = 60U * 1000U;
static constexpr uint64_t OBSERVE_MS = 60U * 1000U;
// The counter median/smoothing pipeline still contains samples from the old
// clock immediately after a PLL transition. Do not interpret that expected
// lag as lost silicon throughput.
static constexpr uint64_t PROBE_SETTLE_MS = 45U * 1000U;
static constexpr uint64_t COOLDOWN_MS = 10U * 60U * 1000U;

enum class State : uint8_t {
    DISABLED,
    WARMUP,
    OBSERVE,
    PROBE,
    COOLDOWN,
};

enum class Reason : uint8_t {
    DISABLED,
    WARMING_UP,
    OBSERVING,
    PROBE_REQUESTED,
    PROBE_IN_PROGRESS,
    PROBE_ACCEPTED,
    AT_FREQUENCY_CAP,
    COOLDOWN,
    COOLDOWN_COMPLETE,
    INVALID_SAMPLE,
    TELEMETRY_STALE,
    POWER_LIMIT,
    CURRENT_LIMIT,
    CHIP_TEMPERATURE_LIMIT,
    VR_TEMPERATURE_LIMIT,
    FAN_GUARD,
    POOL_GUARD,
    CHIP_GUARD,
    LOW_HASHRATE,
    REJECT_DURING_PROBE,
    DUPLICATE_DURING_PROBE,
    PREDICTED_POWER_LIMIT,
};

/**
 * Runtime limits supplied by the board integration.
 *
 * The governor intentionally contains no board-specific power, current or
 * temperature thresholds. All limits are strict upper bounds: a sample equal
 * to a limit is considered unsafe.
 */
struct Limits {
    double powerLimitWatts = 0.0;
    double currentLimitAmps = 0.0;
    double chipTemperatureLimitC = 0.0;
    double vrTemperatureLimitC = 0.0;
    uint16_t expectedChips = 0;
    uint64_t maxTelemetryAgeMs = 0;
};

/** A coherent runtime snapshot. Counters must be monotonically increasing. */
struct Sample {
    uint64_t nowMs = 0;
    double frequencyMhz = 0.0;
    double hashrateGhs = 0.0;
    double powerWatts = 0.0;
    double currentAmps = 0.0;
    double chipTemperatureC = 0.0;
    double vrTemperatureC = 0.0;
    uint16_t detectedChips = 0;
    bool fanHealthy = false;
    bool poolReady = false;
    bool telemetryValid = false;
    uint64_t telemetryAgeMs = 0;
    uint64_t rejectedShares = 0;
    uint64_t duplicateShares = 0;
};

/**
 * Pure policy output. The caller remains responsible for applying the target.
 * targetFrequencyMhz is always one of the configured, allowed options.
 */
struct Decision {
    uint16_t targetFrequencyMhz = 0;
    bool changeRequested = false;
    bool emergency = false;
    Reason reason = Reason::DISABLED;
    double predictedPowerWatts = 0.0;
};

class Governor {
  public:
    /**
     * Configures the pure policy. Frequencies above logicalCapMhz are ignored,
     * as is 575 MHz regardless of the cap. The persistent base must itself be
     * present in the resulting option set.
     */
    bool configure(const std::vector<uint16_t> &frequencyOptions,
                   uint16_t persistentBaseMhz,
                   const Limits &limits,
                   uint16_t logicalCapMhz = DEFAULT_LOGICAL_CAP_MHZ);

    /** Opt-in switch. Enabling always starts a fresh warmup at the base. */
    bool setEnabled(bool enabled, uint64_t nowMs);

    /** Evaluates one snapshot without mutating hardware or persistent state. */
    Decision update(const Sample &sample);

    State state() const { return m_state; }
    bool enabled() const { return m_enabled; }
    bool configured() const { return m_configured; }
    uint16_t persistentBaseMhz() const { return m_persistentBaseMhz; }
    uint16_t runtimeTargetMhz() const { return m_runtimeTargetMhz; }
    uint16_t stableRuntimeMhz() const { return m_stableRuntimeMhz; }
    uint16_t logicalCapMhz() const { return m_logicalCapMhz; }
    const std::vector<uint16_t> &frequencyOptions() const { return m_frequencyOptions; }

    bool isFrequencyAllowed(uint16_t frequencyMhz) const;

    /** BM1368 theoretical rate: chips * 1276 small cores * MHz / 1000. */
    static double theoreticalHashrateGhs(uint16_t chips, double frequencyMhz);
    static double hashrateRatio(const Sample &sample);
    static const char *reasonString(Reason reason);

  private:
    struct GuardFailure {
        bool failed = false;
        bool physical = false;
        Reason reason = Reason::OBSERVING;
    };

    // Require an almost-perfect base point before spending power on a probe.
    // The verified 550MHz point on real NerdQAxe+ hardware reaches about 98%
    // of the BM1368 theoretical counter rate while still improving absolute
    // throughput, so probe acceptance uses a separate empirical floor.
    static constexpr double MIN_BASE_ASCENT_RATIO = 0.995;
    static constexpr double MIN_PROBE_ACCEPT_RATIO = 0.975;
    static constexpr double ROLLBACK_RATIO = 0.965;
    static constexpr uint8_t LOW_RATIO_SAMPLE_LIMIT = 3;
    static constexpr double FREQUENCY_EPSILON_MHZ = 0.01;

    std::vector<uint16_t> m_frequencyOptions;
    Limits m_limits;
    bool m_configured = false;
    bool m_enabled = false;
    State m_state = State::DISABLED;

    uint16_t m_logicalCapMhz = DEFAULT_LOGICAL_CAP_MHZ;
    uint16_t m_persistentBaseMhz = 0;
    uint16_t m_runtimeTargetMhz = 0;
    uint16_t m_stableRuntimeMhz = 0;
    uint16_t m_probeTargetMhz = 0;

    bool m_phaseStarted = false;
    uint64_t m_phaseStartedMs = 0;
    bool m_probeReached = false;
    uint64_t m_probeReachedMs = 0;

    bool m_haveLastUpdate = false;
    uint64_t m_lastUpdateMs = 0;
    uint8_t m_lowRatioSamples = 0;
    uint64_t m_probeRejectedBaseline = 0;
    uint64_t m_probeDuplicateBaseline = 0;

    bool validLimits(const Limits &limits) const;
    bool validSample(const Sample &sample) const;
    bool sameFrequency(double actualMhz, uint16_t targetMhz) const;
    bool telemetryFresh(const Sample &sample) const;
    GuardFailure guardFailure(const Sample &sample) const;

    uint16_t nextFrequency(uint16_t currentMhz) const;
    uint16_t previousFrequency(double currentMhz) const;
    uint16_t rollbackTarget(const Sample &sample) const;
    double predictedPower(const Sample &sample, uint16_t targetMhz) const;

    void resetPhase();
    void startPhase(uint64_t nowMs);
    bool phaseElapsed(uint64_t nowMs, uint64_t durationMs) const;
    void clearProbe();

    Decision hold(const Sample &sample, Reason reason, bool emergency = false) const;
    Decision request(const Sample &sample, uint16_t targetMhz, Reason reason,
                     bool emergency);
    Decision rollback(const Sample &sample, Reason reason);
};

} // namespace HashrateGovernor
