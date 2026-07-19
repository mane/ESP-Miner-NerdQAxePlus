from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[1]


HARNESS = r"""
#include "hashrate_governor.h"

#include <cassert>
#include <cmath>
#include <vector>

using namespace HashrateGovernor;

static Limits limits()
{
    Limits value;
    value.powerLimitWatts = 69.0;
    value.currentLimitAmps = 5.9;
    value.chipTemperatureLimitC = 65.0;
    value.vrTemperatureLimitC = 75.0;
    value.expectedChips = 4;
    value.maxTelemetryAgeMs = 12000;
    return value;
}

static Sample healthy(uint64_t now, double frequency = 525.0)
{
    Sample sample;
    sample.nowMs = now;
    sample.frequencyMhz = frequency;
    sample.hashrateGhs = Governor::theoreticalHashrateGhs(4, frequency);
    sample.powerWatts = 60.0;
    sample.currentAmps = 5.0;
    sample.chipTemperatureC = 55.0;
    sample.vrTemperatureC = 65.0;
    sample.detectedChips = 4;
    sample.fanHealthy = true;
    sample.poolReady = true;
    sample.telemetryValid = true;
    sample.telemetryAgeMs = 0;
    return sample;
}

static Governor configuredGovernor()
{
    Governor governor;
    assert(governor.configure({575, 550, 525, 500, 490, 525}, 525, limits()));
    return governor;
}

static Decision reachProbe(Governor &governor)
{
    assert(governor.setEnabled(true, 0));
    Decision decision = governor.update(healthy(0));
    assert(decision.reason == Reason::WARMING_UP);
    governor.update(healthy(WARMUP_MS));
    assert(governor.state() == State::OBSERVE);
    decision = governor.update(healthy(WARMUP_MS + OBSERVE_MS));
    assert(governor.state() == State::PROBE);
    assert(decision.changeRequested);
    assert(decision.targetFrequencyMhz == 550);
    assert(decision.reason == Reason::PROBE_REQUESTED);
    return decision;
}

static void testConfigurationAndMath()
{
    Governor governor = configuredGovernor();
    assert(governor.configured());
    assert(!governor.enabled());
    assert(governor.persistentBaseMhz() == 525);
    assert(governor.logicalCapMhz() == 550);
    assert(governor.isFrequencyAllowed(490));
    assert(governor.isFrequencyAllowed(550));
    assert(!governor.isFrequencyAllowed(575));
    assert(governor.frequencyOptions().size() == 4);
    assert(std::fabs(Governor::theoreticalHashrateGhs(4, 490.0) - 2500.96) < 0.001);

    Sample measured = healthy(0, 490.0);
    measured.hashrateGhs = 2500.70;
    assert(Governor::hashrateRatio(measured) > 0.9998);

    Governor invalid;
    assert(!invalid.configure({490, 550, 575}, 575, limits(), 650));
}

static void testOptInAndSuccessfulProbeKeepsPersistentBase()
{
    Governor governor = configuredGovernor();
    Decision disabled = governor.update(healthy(0));
    assert(disabled.reason == Reason::DISABLED);
    assert(!disabled.changeRequested);

    reachProbe(governor);
    Sample verifiedProbe = healthy(WARMUP_MS + OBSERVE_MS + 1, 550.0);
    verifiedProbe.hashrateGhs *= 0.980;
    Decision inProgress = governor.update(verifiedProbe);
    assert(inProgress.reason == Reason::PROBE_IN_PROGRESS);

    verifiedProbe.nowMs = WARMUP_MS + 2 * OBSERVE_MS + 1;
    Decision accepted = governor.update(verifiedProbe);
    assert(accepted.reason == Reason::PROBE_ACCEPTED);
    assert(governor.state() == State::OBSERVE);
    assert(governor.runtimeTargetMhz() == 550);
    assert(governor.stableRuntimeMhz() == 550);
    assert(governor.persistentBaseMhz() == 525);
}

static void testPredictedPowerBlocksProbe()
{
    Limits restrictive = limits();
    restrictive.powerLimitWatts = 67.5;
    Governor governor;
    assert(governor.configure({490, 525, 550, 575}, 525, restrictive));
    assert(governor.setEnabled(true, 0));

    Sample sample = healthy(0);
    sample.powerWatts = 65.3;
    governor.update(sample);
    sample.nowMs = WARMUP_MS;
    governor.update(sample);
    sample.nowMs += OBSERVE_MS;
    Decision decision = governor.update(sample);

    assert(decision.reason == Reason::PREDICTED_POWER_LIMIT);
    assert(!decision.changeRequested);
    assert(decision.targetFrequencyMhz == 525);
    assert(decision.predictedPowerWatts > restrictive.powerLimitWatts);
    assert(governor.state() == State::OBSERVE);
}

static void testVerified525To550ProbeFits69WEnvelope()
{
    Governor governor = configuredGovernor();
    assert(governor.setEnabled(true, 0));

    Sample sample = healthy(0);
    sample.powerWatts = 65.25;
    governor.update(sample);
    sample.nowMs = WARMUP_MS;
    governor.update(sample);
    sample.nowMs += OBSERVE_MS;
    Decision decision = governor.update(sample);

    assert(decision.reason == Reason::PROBE_REQUESTED);
    assert(decision.targetFrequencyMhz == 550);
    assert(decision.predictedPowerWatts > 68.3);
    assert(decision.predictedPowerWatts < 69.0);
}

static void testQualified540StepAndProbeRollback()
{
    Governor governor;
    assert(governor.configure({525, 540, 550, 575}, 525, limits()));
    assert(governor.setEnabled(true, 0));

    governor.update(healthy(0));
    governor.update(healthy(WARMUP_MS));
    Decision firstProbe = governor.update(healthy(WARMUP_MS + OBSERVE_MS));
    assert(firstProbe.reason == Reason::PROBE_REQUESTED);
    assert(firstProbe.targetFrequencyMhz == 540);

    Sample at540 = healthy(WARMUP_MS + OBSERVE_MS + 1, 540.0);
    governor.update(at540);
    at540.nowMs += OBSERVE_MS;
    Decision accepted540 = governor.update(at540);
    assert(accepted540.reason == Reason::PROBE_ACCEPTED);
    assert(governor.runtimeTargetMhz() == 540);
    assert(governor.stableRuntimeMhz() == 540);

    at540.nowMs += OBSERVE_MS;
    Decision secondProbe = governor.update(at540);
    assert(secondProbe.reason == Reason::PROBE_REQUESTED);
    assert(secondProbe.targetFrequencyMhz == 550);

    Sample hot550 = healthy(at540.nowMs + 1, 550.0);
    hot550.vrTemperatureC = limits().vrTemperatureLimitC;
    Decision rollback = governor.update(hot550);
    assert(rollback.reason == Reason::VR_TEMPERATURE_LIMIT);
    assert(rollback.emergency);
    assert(rollback.targetFrequencyMhz == 540);
}

static void testFreshTelemetryAndRatioGateAscent()
{
    Governor staleGovernor = configuredGovernor();
    assert(staleGovernor.setEnabled(true, 0));
    Sample stale = healthy(0);
    stale.telemetryAgeMs = limits().maxTelemetryAgeMs + 1;
    Decision staleDecision = staleGovernor.update(stale);
    assert(staleDecision.reason == Reason::TELEMETRY_STALE);
    assert(staleGovernor.state() == State::WARMUP);

    Governor slowGovernor = configuredGovernor();
    assert(slowGovernor.setEnabled(true, 0));
    Sample slow = healthy(0);
    slow.hashrateGhs *= 0.990;
    slowGovernor.update(slow);
    slow.nowMs = WARMUP_MS;
    slowGovernor.update(slow);
    slow.nowMs += OBSERVE_MS;
    Decision slowDecision = slowGovernor.update(slow);
    assert(slowDecision.reason == Reason::OBSERVING);
    assert(!slowDecision.changeRequested);
    assert(slowGovernor.state() == State::OBSERVE);
}

static void testLowRatioAtPersistentBaseNeverDownclocks()
{
    Governor governor = configuredGovernor();
    assert(governor.setEnabled(true, 0));

    Sample slow = healthy(0);
    slow.hashrateGhs *= 0.80;
    for (uint64_t now = 0; now <= 10000; now += 5000) {
        slow.nowMs = now;
        Decision decision = governor.update(slow);
        assert(!decision.emergency);
        assert(decision.targetFrequencyMhz == 525);
    }
    assert(governor.runtimeTargetMhz() == governor.persistentBaseMhz());
    assert(governor.state() == State::WARMUP);
}

static void testProbeRejectAndDuplicateRollback()
{
    Governor rejectedDuringRamp = configuredGovernor();
    reachProbe(rejectedDuringRamp);
    Sample rampReject = healthy(WARMUP_MS + OBSERVE_MS + 1, 525.0);
    rampReject.rejectedShares = 1;
    Decision rampDecision = rejectedDuringRamp.update(rampReject);
    assert(rampDecision.reason == Reason::REJECT_DURING_PROBE);
    assert(rampDecision.targetFrequencyMhz == 525);

    Governor rejected = configuredGovernor();
    reachProbe(rejected);
    Sample reject = healthy(WARMUP_MS + OBSERVE_MS + 1, 550.0);
    reject.rejectedShares = 1;
    Decision rejectDecision = rejected.update(reject);
    assert(rejectDecision.reason == Reason::REJECT_DURING_PROBE);
    assert(rejectDecision.emergency);
    assert(rejectDecision.targetFrequencyMhz == 525);
    assert(rejected.state() == State::COOLDOWN);

    Governor duplicate = configuredGovernor();
    reachProbe(duplicate);
    Sample dup = healthy(WARMUP_MS + OBSERVE_MS + 1, 550.0);
    dup.duplicateShares = 1;
    Decision duplicateDecision = duplicate.update(dup);
    assert(duplicateDecision.reason == Reason::DUPLICATE_DURING_PROBE);
    assert(duplicateDecision.emergency);
    assert(duplicateDecision.targetFrequencyMhz == 525);
}

static void testThreeLowRatioSamplesRollback()
{
    Governor governor = configuredGovernor();
    reachProbe(governor);

    Sample sample = healthy(WARMUP_MS + OBSERVE_MS + 1, 550.0);
    sample.hashrateGhs *= 0.960;
    Decision settling = governor.update(sample);
    assert(settling.reason == Reason::PROBE_IN_PROGRESS);

    // The rolling counter filter initially contains samples measured at the
    // old clock. Low-ratio rollback is deliberately disarmed while it settles.
    sample.nowMs += PROBE_SETTLE_MS - 1;
    Decision stillSettling = governor.update(sample);
    assert(stillSettling.reason == Reason::PROBE_IN_PROGRESS);
    assert(!stillSettling.emergency);

    sample.nowMs += 1;
    Decision first = governor.update(sample);
    assert(first.reason == Reason::PROBE_IN_PROGRESS);
    sample.nowMs += 5000;
    governor.update(sample);
    sample.nowMs += 5000;
    Decision third = governor.update(sample);

    assert(third.reason == Reason::LOW_HASHRATE);
    assert(third.emergency);
    assert(third.targetFrequencyMhz == 525);
    assert(governor.state() == State::COOLDOWN);
}

static void testRuntimeGuardsAndCooldown()
{
    Governor governor = configuredGovernor();
    reachProbe(governor);
    Sample hot = healthy(WARMUP_MS + OBSERVE_MS + 1, 550.0);
    hot.chipTemperatureC = limits().chipTemperatureLimitC;
    Decision rollback = governor.update(hot);
    assert(rollback.reason == Reason::CHIP_TEMPERATURE_LIMIT);
    assert(rollback.emergency);
    assert(rollback.targetFrequencyMhz == 525);

    Sample safe = healthy(hot.nowMs + COOLDOWN_MS - 1, 525.0);
    Decision cooling = governor.update(safe);
    assert(cooling.reason == Reason::COOLDOWN);
    safe.nowMs = hot.nowMs + COOLDOWN_MS;
    Decision complete = governor.update(safe);
    assert(complete.reason == Reason::COOLDOWN_COMPLETE);
    assert(governor.state() == State::WARMUP);

    Governor pool = configuredGovernor();
    reachProbe(pool);
    Sample disconnected = healthy(WARMUP_MS + OBSERVE_MS + 1, 550.0);
    disconnected.poolReady = false;
    Decision poolRollback = pool.update(disconnected);
    assert(poolRollback.reason == Reason::POOL_GUARD);
    assert(poolRollback.emergency);
    assert(poolRollback.targetFrequencyMhz == 525);

    Governor chip = configuredGovernor();
    assert(chip.setEnabled(true, 0));
    Sample missing = healthy(0);
    missing.detectedChips = 3;
    missing.hashrateGhs = Governor::theoreticalHashrateGhs(3, 525.0);
    Decision chipDecision = chip.update(missing);
    assert(chipDecision.reason == Reason::CHIP_GUARD);
    assert(chipDecision.emergency);
    assert(chipDecision.targetFrequencyMhz == 500);
}

static void testPersistentPhysicalFaultStopsAtMinimumFrequency()
{
    Governor governor;
    assert(governor.configure({400, 425, 450, 475, 490, 500, 525, 550},
                              525, limits()));
    assert(governor.setEnabled(true, 0));

    const uint16_t expectedTargets[] = {500, 490, 475, 450, 425, 400, 400, 400};
    double actualFrequency = 525.0;
    uint64_t now = 0;
    for (uint16_t expected : expectedTargets) {
        Sample fault = healthy(now, actualFrequency);
        fault.fanHealthy = false;
        Decision decision = governor.update(fault);
        assert(decision.reason == Reason::FAN_GUARD);
        assert(decision.emergency);
        assert(decision.targetFrequencyMhz == expected);
        actualFrequency = expected;
        now += 2000;
    }
}

int main()
{
    testConfigurationAndMath();
    testOptInAndSuccessfulProbeKeepsPersistentBase();
    testPredictedPowerBlocksProbe();
    testVerified525To550ProbeFits69WEnvelope();
    testQualified540StepAndProbeRollback();
    testFreshTelemetryAndRatioGateAscent();
    testLowRatioAtPersistentBaseNeverDownclocks();
    testProbeRejectAndDuplicateRollback();
    testThreeLowRatioSamplesRollback();
    testRuntimeGuardsAndCooldown();
    testPersistentPhysicalFaultStopsAtMinimumFrequency();
    return 0;
}
"""


class HashrateGovernorTest(unittest.TestCase):
    def test_pure_governor_contract_and_state_machine(self) -> None:
        compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "A C++ compiler is required for this test")

        with tempfile.TemporaryDirectory(prefix="hashrate-governor-") as temp_dir:
            temp = Path(temp_dir)
            harness = temp / "hashrate_governor_harness.cpp"
            binary = temp / "hashrate_governor_harness"
            harness.write_text(textwrap.dedent(HARNESS))

            build = subprocess.run(
                [
                    compiler,
                    "-std=c++11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(REPO / "main"),
                    str(harness),
                    str(REPO / "main/hashrate_governor.cpp"),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

            run = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_module_has_no_firmware_or_persistence_dependencies(self) -> None:
        source = (REPO / "main/hashrate_governor.cpp").read_text()
        header = (REPO / "main/hashrate_governor.h").read_text()
        combined = source + header

        for forbidden in (
            "freertos/",
            "esp_",
            "nvs_",
            "Config::",
            "Board::",
            "POWER_MANAGEMENT_MODULE",
        ):
            self.assertNotIn(forbidden, combined)

        self.assertIn("DEFAULT_LOGICAL_CAP_MHZ = 550", header)
        self.assertIn("ALWAYS_EXCLUDED_FREQUENCY_MHZ = 575", header)
        self.assertIn("chips) * BM1368_SMALL_CORES * frequencyMhz / 1000.0", source)


if __name__ == "__main__":
    unittest.main()
