#include "hashrate_governor.h"

#include <algorithm>
#include <cmath>

namespace HashrateGovernor {

namespace {

constexpr double BM1368_SMALL_CORES = 1276.0;

bool finitePositive(double value)
{
    return std::isfinite(value) && value > 0.0;
}

std::vector<uint16_t> normalizedFrequencies(const std::vector<uint16_t> &frequencyOptions)
{
    std::vector<uint16_t> normalized;
    normalized.reserve(frequencyOptions.size());
    for (uint16_t frequency : frequencyOptions) {
        if (frequency != 0 && frequency != ALWAYS_EXCLUDED_FREQUENCY_MHZ) {
            normalized.push_back(frequency);
        }
    }
    std::sort(normalized.begin(), normalized.end());
    normalized.erase(std::unique(normalized.begin(), normalized.end()), normalized.end());
    return normalized;
}

std::vector<uint16_t> cappedFrequencies(const std::vector<uint16_t> &supported,
                                        uint16_t logicalCapMhz)
{
    std::vector<uint16_t> allowed;
    allowed.reserve(supported.size());
    for (uint16_t frequency : supported) {
        if (frequency <= logicalCapMhz) {
            allowed.push_back(frequency);
        }
    }
    return allowed;
}

bool sameLimits(const Limits &left, const Limits &right)
{
    return left.powerLimitWatts == right.powerLimitWatts &&
           left.currentLimitAmps == right.currentLimitAmps &&
           left.chipTemperatureLimitC == right.chipTemperatureLimitC &&
           left.vrTemperatureLimitC == right.vrTemperatureLimitC &&
           left.expectedChips == right.expectedChips &&
           left.maxTelemetryAgeMs == right.maxTelemetryAgeMs;
}

} // namespace

bool Governor::configure(const std::vector<uint16_t> &frequencyOptions,
                         uint16_t persistentBaseMhz,
                         const Limits &limits,
                         uint16_t logicalCapMhz)
{
    if (!validLimits(limits) || logicalCapMhz == 0) {
        return false;
    }

    std::vector<uint16_t> supported = normalizedFrequencies(frequencyOptions);
    std::vector<uint16_t> allowed = cappedFrequencies(supported, logicalCapMhz);
    if (allowed.empty() ||
        !std::binary_search(allowed.begin(), allowed.end(), persistentBaseMhz)) {
        return false;
    }

    m_frequencyOptions = allowed;
    m_supportedFrequencyOptions = supported;
    m_limits = limits;
    m_logicalCapMhz = logicalCapMhz;
    m_persistentBaseMhz = persistentBaseMhz;
    m_runtimeTargetMhz = persistentBaseMhz;
    m_stableRuntimeMhz = persistentBaseMhz;
    m_probeTargetMhz = 0;
    m_configured = true;
    m_enabled = false;
    m_state = State::DISABLED;
    m_haveLastUpdate = false;
    m_lowRatioSamples = 0;
    resetPhase();
    clearProbe();
    return true;
}

bool Governor::configurationMatches(const std::vector<uint16_t> &frequencyOptions,
                                    uint16_t persistentBaseMhz,
                                    const Limits &limits,
                                    uint16_t logicalCapMhz) const
{
    if (!m_configured || logicalCapMhz == 0 || persistentBaseMhz != m_persistentBaseMhz ||
        logicalCapMhz != m_logicalCapMhz || !sameLimits(limits, m_limits)) {
        return false;
    }

    const std::vector<uint16_t> supported = normalizedFrequencies(frequencyOptions);
    return supported == m_supportedFrequencyOptions &&
           cappedFrequencies(supported, logicalCapMhz) == m_frequencyOptions;
}

bool Governor::reconfigureCapPreservingStable(
    const std::vector<uint16_t> &frequencyOptions,
    uint16_t persistentBaseMhz,
    const Limits &limits,
    uint16_t logicalCapMhz,
    const Sample &sample)
{
    if (!m_configured || !m_enabled || logicalCapMhz == 0 ||
        logicalCapMhz == m_logicalCapMhz || persistentBaseMhz != m_persistentBaseMhz ||
        !validLimits(limits) || !sameLimits(limits, m_limits) ||
        (m_state != State::OBSERVE && m_state != State::PROBE)) {
        return false;
    }

    const std::vector<uint16_t> supported = normalizedFrequencies(frequencyOptions);
    if (supported != m_supportedFrequencyOptions) {
        return false;
    }
    std::vector<uint16_t> allowed = cappedFrequencies(supported, logicalCapMhz);
    if (allowed.empty() ||
        !std::binary_search(allowed.begin(), allowed.end(), persistentBaseMhz)) {
        return false;
    }

    // A cap edit must never mask an unsafe or out-of-order sample. The normal
    // full reconfiguration path will then restart at the persistent base.
    if ((m_haveLastUpdate && sample.nowMs < m_lastUpdateMs) || !validSample(sample) ||
        !telemetryFresh(sample) || guardFailure(sample).failed ||
        hashrateRatio(sample) < ROLLBACK_RATIO) {
        return false;
    }

    // OBSERVE represents a settled point and must agree with the physical PLL.
    // A PROBE may legitimately be in flight; it is aborted below to the last
    // stable point. Do not let a cap edit hide a reject/duplicate from that probe.
    if (m_state == State::OBSERVE &&
        (m_runtimeTargetMhz != m_stableRuntimeMhz ||
         !sameFrequency(sample.frequencyMhz, m_stableRuntimeMhz))) {
        return false;
    }
    if (m_state == State::PROBE &&
        (sample.rejectedShares > m_probeRejectedBaseline ||
         sample.duplicateShares > m_probeDuplicateBaseline)) {
        return false;
    }

    auto stableOrLower = std::upper_bound(
        allowed.begin(), allowed.end(), m_stableRuntimeMhz);
    if (stableOrLower == allowed.begin()) {
        return false;
    }
    --stableOrLower;
    const uint16_t preservedTarget = *stableOrLower;
    if (preservedTarget < persistentBaseMhz) {
        return false;
    }

    m_frequencyOptions = allowed;
    m_logicalCapMhz = logicalCapMhz;
    m_runtimeTargetMhz = preservedTarget;
    m_stableRuntimeMhz = preservedTarget;
    m_lowRatioSamples = 0;
    clearProbe();

    // A cap increase at an already-settled point needs only a fresh observation
    // window. A clamped/in-flight point first has to reach its target, so retain
    // the normal warmup before it can probe again.
    if (sameFrequency(sample.frequencyMhz, preservedTarget)) {
        m_state = State::OBSERVE;
        startPhase(sample.nowMs);
    } else {
        m_state = State::WARMUP;
        resetPhase();
    }
    return true;
}

bool Governor::setEnabled(bool enabled, uint64_t nowMs)
{
    if (enabled && !m_configured) {
        return false;
    }

    m_enabled = enabled;
    m_haveLastUpdate = false;
    m_lowRatioSamples = 0;
    resetPhase();
    clearProbe();

    if (!enabled) {
        m_runtimeTargetMhz = m_persistentBaseMhz;
        m_stableRuntimeMhz = m_persistentBaseMhz;
        m_state = State::DISABLED;
        return true;
    }

    m_runtimeTargetMhz = m_persistentBaseMhz;
    m_stableRuntimeMhz = m_persistentBaseMhz;
    m_state = State::WARMUP;
    m_phaseStartedMs = nowMs;
    return true;
}

Decision Governor::update(const Sample &sample)
{
    if (!m_configured || !m_enabled) {
        return hold(sample, Reason::DISABLED);
    }

    if (m_haveLastUpdate && sample.nowMs < m_lastUpdateMs) {
        resetPhase();
        clearProbe();
        m_state = State::WARMUP;
        m_runtimeTargetMhz = m_persistentBaseMhz;
        m_stableRuntimeMhz = m_persistentBaseMhz;
        m_lowRatioSamples = 0;
        return request(sample, m_persistentBaseMhz, Reason::INVALID_SAMPLE, true);
    }
    m_haveLastUpdate = true;
    m_lastUpdateMs = sample.nowMs;

    if (!validSample(sample)) {
        resetPhase();
        m_lowRatioSamples = 0;
        if (m_state == State::PROBE || m_runtimeTargetMhz > m_persistentBaseMhz) {
            return rollback(sample, Reason::INVALID_SAMPLE);
        }
        return hold(sample, Reason::INVALID_SAMPLE);
    }

    GuardFailure guard = guardFailure(sample);
    if (guard.failed) {
        resetPhase();
        m_lowRatioSamples = 0;

        // A failed probe or an elevated runtime point always returns to the
        // persistent base. Physical faults at the base step down once more when
        // a lower configured option exists. Readiness guards merely block ascent.
        if (m_state == State::PROBE || m_runtimeTargetMhz > m_persistentBaseMhz ||
            guard.physical) {
            uint16_t target = rollbackTarget(sample);
            if (target != m_runtimeTargetMhz || m_state == State::PROBE) {
                return rollback(sample, guard.reason);
            }
        }
        return hold(sample, guard.reason, guard.physical);
    }

    // Never downclock below the persistent base merely because the hardware
    // counter is still warming up. Ratio rollback is armed only while assessing
    // a probe or while running above the persistent base. At the base, a weak
    // ratio still prevents ascent in the OBSERVE state below.
    const bool probeHashrateSettled = m_state != State::PROBE ||
        (m_probeReached && sample.nowMs >= m_probeReachedMs &&
         (sample.nowMs - m_probeReachedMs) >= PROBE_SETTLE_MS);
    const bool ratioRollbackArmed =
        (m_state == State::PROBE && probeHashrateSettled) ||
        (m_state != State::PROBE && m_runtimeTargetMhz > m_persistentBaseMhz);
    if (ratioRollbackArmed && sameFrequency(sample.frequencyMhz, m_runtimeTargetMhz) &&
        telemetryFresh(sample)) {
        double ratio = hashrateRatio(sample);
        if (ratio < ROLLBACK_RATIO) {
            if (m_lowRatioSamples < LOW_RATIO_SAMPLE_LIMIT) {
                ++m_lowRatioSamples;
            }
        } else {
            m_lowRatioSamples = 0;
        }

        if (m_lowRatioSamples >= LOW_RATIO_SAMPLE_LIMIT) {
            return rollback(sample, Reason::LOW_HASHRATE);
        }
    } else {
        m_lowRatioSamples = 0;
    }

    if (m_state == State::COOLDOWN) {
        if (!m_phaseStarted) {
            startPhase(sample.nowMs);
        }
        if (!phaseElapsed(sample.nowMs, COOLDOWN_MS)) {
            return hold(sample, Reason::COOLDOWN);
        }

        m_state = State::WARMUP;
        resetPhase();
        m_lowRatioSamples = 0;
        m_stableRuntimeMhz = m_persistentBaseMhz;
        return request(sample, m_persistentBaseMhz, Reason::COOLDOWN_COMPLETE, false);
    }

    if (m_state == State::WARMUP) {
        if (!sameFrequency(sample.frequencyMhz, m_runtimeTargetMhz)) {
            resetPhase();
            return request(sample, m_runtimeTargetMhz, Reason::WARMING_UP, false);
        }
        if (!m_phaseStarted) {
            startPhase(sample.nowMs);
        }
        if (!phaseElapsed(sample.nowMs, WARMUP_MS)) {
            return hold(sample, Reason::WARMING_UP);
        }

        m_state = State::OBSERVE;
        startPhase(sample.nowMs);
        return hold(sample, Reason::OBSERVING);
    }

    if (m_state == State::OBSERVE) {
        if (!sameFrequency(sample.frequencyMhz, m_runtimeTargetMhz)) {
            resetPhase();
            return request(sample, m_runtimeTargetMhz, Reason::OBSERVING, false);
        }
        if (!m_phaseStarted) {
            startPhase(sample.nowMs);
        }
        if (!phaseElapsed(sample.nowMs, OBSERVE_MS)) {
            return hold(sample, Reason::OBSERVING);
        }

        uint16_t next = nextFrequency(m_runtimeTargetMhz);
        if (next == 0) {
            startPhase(sample.nowMs);
            return hold(sample, Reason::AT_FREQUENCY_CAP);
        }

        if (hashrateRatio(sample) < MIN_BASE_ASCENT_RATIO) {
            return hold(sample, Reason::OBSERVING);
        }

        double nextPower = predictedPower(sample, next);
        if (!std::isfinite(nextPower) || !(sample.powerWatts < m_limits.powerLimitWatts) ||
            !(nextPower < m_limits.powerLimitWatts)) {
            startPhase(sample.nowMs);
            Decision decision = hold(sample, Reason::PREDICTED_POWER_LIMIT);
            decision.predictedPowerWatts = nextPower;
            return decision;
        }

        m_probeTargetMhz = next;
        m_probeReached = false;
        m_probeReachedMs = 0;
        m_probeRejectedBaseline = sample.rejectedShares;
        m_probeDuplicateBaseline = sample.duplicateShares;
        m_lowRatioSamples = 0;
        m_state = State::PROBE;
        return request(sample, next, Reason::PROBE_REQUESTED, false);
    }

    if (m_state == State::PROBE) {
        if (sample.rejectedShares < m_probeRejectedBaseline) {
            m_probeRejectedBaseline = sample.rejectedShares;
        } else if (sample.rejectedShares > m_probeRejectedBaseline) {
            return rollback(sample, Reason::REJECT_DURING_PROBE);
        }

        if (sample.duplicateShares < m_probeDuplicateBaseline) {
            m_probeDuplicateBaseline = sample.duplicateShares;
        } else if (sample.duplicateShares > m_probeDuplicateBaseline) {
            return rollback(sample, Reason::DUPLICATE_DURING_PROBE);
        }

        if (!sameFrequency(sample.frequencyMhz, m_probeTargetMhz)) {
            return request(sample, m_probeTargetMhz, Reason::PROBE_IN_PROGRESS, false);
        }

        if (!m_probeReached) {
            m_probeReached = true;
            m_probeReachedMs = sample.nowMs;
        }

        if ((sample.nowMs - m_probeReachedMs) < OBSERVE_MS) {
            return hold(sample, Reason::PROBE_IN_PROGRESS);
        }

        if (hashrateRatio(sample) < MIN_PROBE_ACCEPT_RATIO) {
            return rollback(sample, Reason::LOW_HASHRATE);
        }

        m_stableRuntimeMhz = m_probeTargetMhz;
        m_runtimeTargetMhz = m_probeTargetMhz;
        m_state = State::OBSERVE;
        startPhase(sample.nowMs);
        clearProbe();
        return hold(sample, Reason::PROBE_ACCEPTED);
    }

    return hold(sample, Reason::OBSERVING);
}

bool Governor::isFrequencyAllowed(uint16_t frequencyMhz) const
{
    return std::binary_search(m_frequencyOptions.begin(), m_frequencyOptions.end(), frequencyMhz);
}

double Governor::theoreticalHashrateGhs(uint16_t chips, double frequencyMhz)
{
    if (chips == 0 || !finitePositive(frequencyMhz)) {
        return 0.0;
    }
    return static_cast<double>(chips) * BM1368_SMALL_CORES * frequencyMhz / 1000.0;
}

double Governor::hashrateRatio(const Sample &sample)
{
    double theoretical = theoreticalHashrateGhs(sample.detectedChips, sample.frequencyMhz);
    if (!finitePositive(theoretical) || !std::isfinite(sample.hashrateGhs) ||
        sample.hashrateGhs < 0.0) {
        return 0.0;
    }
    return sample.hashrateGhs / theoretical;
}

const char *Governor::reasonString(Reason reason)
{
    switch (reason) {
        case Reason::DISABLED: return "disabled";
        case Reason::WARMING_UP: return "warming_up";
        case Reason::OBSERVING: return "observing";
        case Reason::PROBE_REQUESTED: return "probe_requested";
        case Reason::PROBE_IN_PROGRESS: return "probe_in_progress";
        case Reason::PROBE_ACCEPTED: return "probe_accepted";
        case Reason::AT_FREQUENCY_CAP: return "at_frequency_cap";
        case Reason::COOLDOWN: return "cooldown";
        case Reason::COOLDOWN_COMPLETE: return "cooldown_complete";
        case Reason::INVALID_SAMPLE: return "invalid_sample";
        case Reason::TELEMETRY_STALE: return "telemetry_stale";
        case Reason::POWER_LIMIT: return "power_limit";
        case Reason::CURRENT_LIMIT: return "current_limit";
        case Reason::CHIP_TEMPERATURE_LIMIT: return "chip_temperature_limit";
        case Reason::VR_TEMPERATURE_LIMIT: return "vr_temperature_limit";
        case Reason::FAN_GUARD: return "fan_guard";
        case Reason::POOL_GUARD: return "pool_guard";
        case Reason::CHIP_GUARD: return "chip_guard";
        case Reason::LOW_HASHRATE: return "low_hashrate";
        case Reason::REJECT_DURING_PROBE: return "reject_during_probe";
        case Reason::DUPLICATE_DURING_PROBE: return "duplicate_during_probe";
        case Reason::PREDICTED_POWER_LIMIT: return "predicted_power_limit";
    }
    return "unknown";
}

bool Governor::validLimits(const Limits &limits) const
{
    return finitePositive(limits.powerLimitWatts) &&
           finitePositive(limits.currentLimitAmps) &&
           finitePositive(limits.chipTemperatureLimitC) &&
           finitePositive(limits.vrTemperatureLimitC) &&
           limits.expectedChips > 0 && limits.maxTelemetryAgeMs > 0;
}

bool Governor::validSample(const Sample &sample) const
{
    return finitePositive(sample.frequencyMhz) &&
           std::isfinite(sample.hashrateGhs) && sample.hashrateGhs >= 0.0 &&
           std::isfinite(sample.powerWatts) && sample.powerWatts >= 0.0 &&
           std::isfinite(sample.currentAmps) && sample.currentAmps >= 0.0 &&
           std::isfinite(sample.chipTemperatureC) &&
           std::isfinite(sample.vrTemperatureC);
}

bool Governor::sameFrequency(double actualMhz, uint16_t targetMhz) const
{
    return std::fabs(actualMhz - static_cast<double>(targetMhz)) <= FREQUENCY_EPSILON_MHZ;
}

bool Governor::telemetryFresh(const Sample &sample) const
{
    return sample.telemetryValid && sample.telemetryAgeMs <= m_limits.maxTelemetryAgeMs;
}

Governor::GuardFailure Governor::guardFailure(const Sample &sample) const
{
    GuardFailure failure;
    if (!telemetryFresh(sample)) {
        failure.failed = true;
        failure.reason = Reason::TELEMETRY_STALE;
        return failure;
    }
    if (!(sample.powerWatts < m_limits.powerLimitWatts)) {
        failure.failed = true;
        failure.physical = true;
        failure.reason = Reason::POWER_LIMIT;
        return failure;
    }
    if (!(sample.currentAmps < m_limits.currentLimitAmps)) {
        failure.failed = true;
        failure.physical = true;
        failure.reason = Reason::CURRENT_LIMIT;
        return failure;
    }
    if (!(sample.chipTemperatureC < m_limits.chipTemperatureLimitC)) {
        failure.failed = true;
        failure.physical = true;
        failure.reason = Reason::CHIP_TEMPERATURE_LIMIT;
        return failure;
    }
    if (!(sample.vrTemperatureC < m_limits.vrTemperatureLimitC)) {
        failure.failed = true;
        failure.physical = true;
        failure.reason = Reason::VR_TEMPERATURE_LIMIT;
        return failure;
    }
    if (!sample.fanHealthy) {
        failure.failed = true;
        failure.physical = true;
        failure.reason = Reason::FAN_GUARD;
        return failure;
    }
    if (sample.detectedChips != m_limits.expectedChips) {
        failure.failed = true;
        failure.physical = true;
        failure.reason = Reason::CHIP_GUARD;
        return failure;
    }
    if (!sample.poolReady) {
        failure.failed = true;
        failure.reason = Reason::POOL_GUARD;
        return failure;
    }
    return failure;
}

uint16_t Governor::nextFrequency(uint16_t currentMhz) const
{
    auto it = std::upper_bound(m_frequencyOptions.begin(), m_frequencyOptions.end(), currentMhz);
    return it == m_frequencyOptions.end() ? 0 : *it;
}

uint16_t Governor::previousFrequency(double currentMhz) const
{
    uint16_t previous = 0;
    for (uint16_t frequency : m_frequencyOptions) {
        if (static_cast<double>(frequency) >= currentMhz - FREQUENCY_EPSILON_MHZ) {
            break;
        }
        previous = frequency;
    }
    return previous;
}

uint16_t Governor::rollbackTarget(const Sample &sample) const
{
    // A failed probe returns to the last point that completed its observation
    // window, even if the asynchronous transition has not reached the candidate
    // yet. This prevents a reject during ramp-up from stepping below the known
    // good point.
    if (m_state == State::PROBE && isFrequencyAllowed(m_stableRuntimeMhz)) {
        return m_stableRuntimeMhz;
    }

    if (sample.frequencyMhz > static_cast<double>(m_persistentBaseMhz) + FREQUENCY_EPSILON_MHZ) {
        return m_persistentBaseMhz;
    }

    uint16_t previous = previousFrequency(sample.frequencyMhz);
    // A persistent physical fault may walk the runtime clock below the
    // persistent base one qualified step at a time. Once the lowest option is
    // reached, hold it: falling back to the base here would create a harmful
    // minimum<->base PLL oscillation while the fault is still present.
    return previous != 0
        ? previous
        : (m_frequencyOptions.empty() ? m_persistentBaseMhz : m_frequencyOptions.front());
}

double Governor::predictedPower(const Sample &sample, uint16_t targetMhz) const
{
    if (!finitePositive(sample.frequencyMhz) || !std::isfinite(sample.powerWatts) ||
        sample.powerWatts < 0.0) {
        return 0.0;
    }
    return sample.powerWatts * static_cast<double>(targetMhz) / sample.frequencyMhz;
}

void Governor::resetPhase()
{
    m_phaseStarted = false;
    m_phaseStartedMs = 0;
}

void Governor::startPhase(uint64_t nowMs)
{
    m_phaseStarted = true;
    m_phaseStartedMs = nowMs;
}

bool Governor::phaseElapsed(uint64_t nowMs, uint64_t durationMs) const
{
    return m_phaseStarted && nowMs >= m_phaseStartedMs &&
           (nowMs - m_phaseStartedMs) >= durationMs;
}

void Governor::clearProbe()
{
    m_probeTargetMhz = 0;
    m_probeReached = false;
    m_probeReachedMs = 0;
    m_probeRejectedBaseline = 0;
    m_probeDuplicateBaseline = 0;
}

Decision Governor::hold(const Sample &sample, Reason reason, bool emergency) const
{
    Decision decision;
    decision.targetFrequencyMhz = m_runtimeTargetMhz;
    decision.changeRequested = false;
    decision.emergency = emergency;
    decision.reason = reason;
    decision.predictedPowerWatts = sample.powerWatts;
    return decision;
}

Decision Governor::request(const Sample &sample, uint16_t targetMhz, Reason reason,
                           bool emergency)
{
    // Every target is selected from the pre-filtered option set. This defensive
    // check prevents a malformed integration from emitting an arbitrary clock.
    if (!isFrequencyAllowed(targetMhz)) {
        return hold(sample, Reason::INVALID_SAMPLE, true);
    }

    m_runtimeTargetMhz = targetMhz;
    Decision decision;
    decision.targetFrequencyMhz = targetMhz;
    decision.changeRequested = !sameFrequency(sample.frequencyMhz, targetMhz);
    decision.emergency = emergency;
    decision.reason = reason;
    decision.predictedPowerWatts = predictedPower(sample, targetMhz);
    return decision;
}

Decision Governor::rollback(const Sample &sample, Reason reason)
{
    uint16_t target = rollbackTarget(sample);
    m_stableRuntimeMhz = target;
    m_runtimeTargetMhz = target;
    m_state = State::COOLDOWN;
    startPhase(sample.nowMs);
    clearProbe();
    m_lowRatioSamples = 0;
    return request(sample, target, reason, true);
}

} // namespace HashrateGovernor
