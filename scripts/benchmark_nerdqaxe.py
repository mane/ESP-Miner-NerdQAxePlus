#!/usr/bin/env python3
"""Read-only NerdQAxe+ benchmark collector and summarizer (Python 3.8+).

The ``collect`` command persists only selected, non-sensitive fields.  In
particular, pool hosts/users, Wi-Fi identifiers and MAC addresses from the API
responses are never written to disk.  The ``summarize`` command expects a file
previously produced by this tool and preserves its existing collection data.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import os
import pathlib
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


SCHEMA = "nerdqaxe-readonly-benchmark-v1"
DEFAULT_URL = os.environ.get("NERDQAXE_URL", "http://nerdqaxe_plus.local/")
MIN_INTERVAL_SECONDS = 1.0
SAFE_GOVERNOR_STATES = {"observe", "cooldown"}
UNSAFE_FINAL_REASONS = {
    "invalid_sample",
    "telemetry_stale",
    "power_limit",
    "current_limit",
    "chip_temperature_limit",
    "vr_temperature_limit",
    "fan_guard",
    "pool_guard",
    "chip_guard",
    "low_hashrate",
    "reject_during_probe",
    "duplicate_during_probe",
    "predicted_power_limit",
}


def normalized_origin(url):
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    return parts.scheme.lower(), parts.hostname, port


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow device redirects only when they stay on the selected origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if normalized_origin(req.full_url) != normalized_origin(newurl):
            raise RuntimeError("API redirect outside the selected device origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


READ_ONLY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    SameOriginRedirectHandler(),
)


def default_output_path():
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return pathlib.Path.cwd() / "nerdqaxe-benchmark-{}.json".format(timestamp)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def scalar(mapping, key, default=None):
    if not isinstance(mapping, dict):
        return default
    return mapping.get(key, default)


def percentile(values, q):
    values = sorted(float(value) for value in values if finite_number(value) is not None)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def round_value(value, digits=4):
    value = finite_number(value)
    return None if value is None else round(value, digits)


def series_stats(values):
    clean = [float(value) for value in values if finite_number(value) is not None]
    if not clean:
        return {"samples": 0}
    ordered = sorted(clean)
    trim = int(len(ordered) * 0.05)
    trimmed = ordered[trim:len(ordered) - trim] if trim and len(ordered) > 2 * trim else ordered
    return {
        "samples": len(clean),
        "mean": round_value(statistics.fmean(clean)),
        "trimmedMean": round_value(statistics.fmean(trimmed)),
        "min": round_value(ordered[0]),
        "p05": round_value(percentile(ordered, 0.05)),
        "p50": round_value(percentile(ordered, 0.50)),
        "p95": round_value(percentile(ordered, 0.95)),
        "max": round_value(ordered[-1]),
    }


def request_json(base_url, path, timeout):
    url = urllib.parse.urljoin(base_url, path)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "Cache-Control": "no-cache"},
    )
    with READ_ONLY_OPENER.open(request, timeout=timeout) as response:
        if normalized_origin(response.geturl()) != normalized_origin(base_url):
            raise RuntimeError("API redirected outside the selected device origin")
        if response.status != 200:
            raise RuntimeError("HTTP status {}".format(response.status))
        return json.load(response)


def sanitized_metadata(identify, system, settings):
    governor = scalar(settings, "hashrateGovernor", {})
    fans = []
    for fan in scalar(settings, "fans", []) or []:
        fans.append(
            {
                "label": scalar(fan, "label"),
                "mode": scalar(fan, "mode"),
                "manualSpeedPercent": scalar(fan, "manualSpeed"),
                "overheatTemperatureC": scalar(fan, "overheatTemp"),
            }
        )
    return {
        "deviceModel": scalar(identify, "deviceModel") or scalar(system, "deviceModel"),
        "asicModel": scalar(system, "asicModel") or scalar(settings, "asicModel"),
        "firmwareVersion": scalar(system, "version") or scalar(settings, "version"),
        "otpEnabled": bool(scalar(identify, "otp", False)),
        "uptimeSecondsAtStart": scalar(system, "uptimeSeconds"),
        "lastResetReason": scalar(system, "lastResetReason"),
        "freeHeapBytesAtStart": scalar(scalar(system, "memory", {}), "freeHeap"),
        "freeInternalHeapBytesAtStart": scalar(scalar(system, "memory", {}), "freeHeapInt"),
        "configuration": {
            "baseFrequencyMHz": scalar(settings, "frequency"),
            "effectiveFrequencyMHz": scalar(settings, "effectiveFrequency"),
            "coreVoltageMillivolts": scalar(settings, "coreVoltage"),
            "frequencyOptionsMHz": scalar(settings, "frequencyOptions", []),
            "governor": {
                "enabled": scalar(governor, "enabled"),
                "maxFrequencyMHz": scalar(governor, "maxFrequency"),
                "powerLimitWatts": scalar(governor, "powerLimitW"),
            },
            "fans": fans,
        },
    }


def sanitize_dashboard(document, elapsed_seconds):
    system = scalar(document, "system", {})
    performance = scalar(document, "performance", {})
    power = scalar(document, "power", {})
    thermal = scalar(document, "thermal", {})
    governor = scalar(performance, "hashrateGovernor", {})
    pools = scalar(scalar(document, "stratum", {}), "pools", []) or []
    active_pools = [pool for pool in pools if scalar(pool, "active", False)]
    connected = any(bool(scalar(pool, "connected", False)) for pool in active_pools)
    if not active_pools:
        connected = any(bool(scalar(pool, "connected", False)) for pool in pools)
    ping_values = [
        finite_number(scalar(pool, "pingRtt"))
        for pool in active_pools
        if finite_number(scalar(pool, "pingRtt")) is not None
    ]
    loss_values = [
        finite_number(scalar(pool, "pingLoss"))
        for pool in active_pools
        if finite_number(scalar(pool, "pingLoss")) is not None
    ]
    asic_count = finite_number(scalar(performance, "asicCount"))
    core_count = finite_number(scalar(performance, "smallCoreCount"))
    actual_frequency = finite_number(scalar(performance, "actualFrequency"))
    theoretical = None
    if asic_count and core_count and actual_frequency:
        theoretical = asic_count * core_count * actual_frequency / 1000.0
    real_hashrate = finite_number(scalar(performance, "hashRate"))
    utilization = real_hashrate / theoretical if real_hashrate is not None and theoretical else None
    watts = finite_number(scalar(power, "watts"))
    efficiency = watts / (real_hashrate / 1000.0) if watts is not None and real_hashrate else None
    vr_external = finite_number(scalar(thermal, "vrTemp"))
    vr_internal = finite_number(scalar(thermal, "vrTempInt"))
    vr_normalized = None
    if vr_external is not None and vr_internal is not None:
        vr_normalized = max(vr_external, vr_internal - 10.0 if vr_internal > 10.0 else vr_internal)
    elif vr_external is not None:
        vr_normalized = vr_external
    elif vr_internal is not None:
        vr_normalized = vr_internal - 10.0 if vr_internal > 10.0 else vr_internal
    chip_hashrates = [
        round_value(value)
        for value in scalar(performance, "chipHashrates", []) or []
        if finite_number(value) is not None
    ]
    chip_ages = [
        round_value(value, 0)
        for value in scalar(performance, "chipHashrateAgesMs", []) or []
        if finite_number(value) is not None
    ]
    return {
        "capturedUtc": utc_now(),
        "elapsedSeconds": round_value(elapsed_seconds, 3),
        "uptimeSeconds": scalar(system, "uptime"),
        "shutdown": bool(scalar(system, "shutdown", False)),
        "boardError": scalar(system, "boardError"),
        "hashrateGhs": round_value(real_hashrate),
        "hashrate1mGhs": round_value(scalar(performance, "hashRate1m")),
        "hashrate10mGhs": round_value(scalar(performance, "hashRate10m")),
        "hashrate1hGhs": round_value(scalar(performance, "hashRate1h")),
        "theoreticalHashrateGhs": round_value(theoretical),
        "calculatedUtilization": round_value(utilization, 6),
        "sharesAccepted": scalar(performance, "sharesAccepted"),
        "sharesRejected": scalar(performance, "sharesRejected"),
        "duplicateHardwareNonces": scalar(performance, "duplicateHWNonces"),
        "shareQueueDrops": scalar(performance, "shareQueueDrops"),
        "configuredFrequencyMHz": scalar(performance, "configuredFrequency"),
        "effectiveFrequencyMHz": scalar(performance, "frequency"),
        "actualFrequencyMHz": round_value(actual_frequency),
        "asicCount": scalar(performance, "asicCount"),
        "smallCoreCount": scalar(performance, "smallCoreCount"),
        "chipHashratesGhs": chip_hashrates,
        "chipHashrateAgesMs": chip_ages,
        "governor": {
            "enabled": bool(scalar(governor, "enabled", False)),
            "targetFrequencyMHz": scalar(governor, "targetFrequency"),
            "lastStableFrequencyMHz": scalar(governor, "lastStableFrequency"),
            "utilization": round_value(scalar(governor, "utilization"), 6),
            "state": scalar(governor, "state"),
            "lastReason": scalar(governor, "lastReason"),
        },
        "powerWatts": round_value(watts),
        "efficiencyJPerTh": round_value(efficiency),
        "inputVoltageVolts": round_value(scalar(power, "voltage")),
        "currentAmps": round_value(scalar(power, "currentA")),
        "actualCoreVoltageVolts": round_value(scalar(power, "coreVoltageActual")),
        "asicTemperatureC": round_value(scalar(thermal, "asicTemp")),
        "externalVrTemperatureC": round_value(vr_external),
        "internalVrTemperatureC": round_value(vr_internal),
        "normalizedVrTemperatureC": round_value(vr_normalized),
        "fanRpm": [scalar(fan, "rpm") for fan in scalar(thermal, "fans", []) or []],
        "fanSpeedPercent": [scalar(fan, "speed") for fan in scalar(thermal, "fans", []) or []],
        "activePoolConnected": connected,
        "activePoolPingMilliseconds": round_value(statistics.fmean(ping_values)) if ping_values else None,
        "activePoolPingLoss": round_value(max(loss_values)) if loss_values else None,
    }


def counter_delta(samples, key):
    values = [sample.get(key) for sample in samples if isinstance(sample.get(key), int)]
    if len(values) < 2:
        return {"delta": None, "resets": 0}
    total = 0
    resets = 0
    for previous, current in zip(values, values[1:]):
        if current >= previous:
            total += current - previous
        else:
            resets += 1
    return {"delta": total, "resets": resets}


def is_operational(sample):
    return (
        sample.get("boardError") in (0, None)
        and not sample.get("shutdown", False)
        and sample.get("activePoolConnected", False)
        and finite_number(sample.get("hashrateGhs")) is not None
        and finite_number(sample.get("powerWatts")) is not None
    )


def stable_signature(sample, tolerance_mhz):
    if not is_operational(sample):
        return None
    governor = sample.get("governor") or {}
    if not governor.get("enabled", False):
        target = sample.get("effectiveFrequencyMHz")
        return ("disabled", target) if finite_number(target) is not None else None
    target = finite_number(governor.get("targetFrequencyMHz"))
    stable = finite_number(governor.get("lastStableFrequencyMHz"))
    actual = finite_number(sample.get("actualFrequencyMHz"))
    state = governor.get("state")
    if target is None or stable is None or actual is None:
        return None
    if target != stable or state not in SAFE_GOVERNOR_STATES:
        return None
    if abs(actual - target) > tolerance_mhz:
        return None
    return (state, int(round(stable)))


def find_stable_blocks(samples, interval_seconds, settle_seconds, tolerance_mhz):
    blocks = []
    current = []
    current_frequency = None
    previous_elapsed = None
    max_gap = max(2.5 * interval_seconds, interval_seconds + 3.0)
    for sample in samples:
        signature = stable_signature(sample, tolerance_mhz)
        elapsed = finite_number(sample.get("elapsedSeconds"))
        frequency = signature[1] if signature else None
        gap = elapsed - previous_elapsed if elapsed is not None and previous_elapsed is not None else 0
        if signature and frequency == current_frequency and gap <= max_gap:
            current.append(sample)
        else:
            if current:
                blocks.append(current)
            current = [sample] if signature else []
            current_frequency = frequency
        previous_elapsed = elapsed
    if current:
        blocks.append(current)

    results = []
    for block in blocks:
        start = finite_number(block[0].get("elapsedSeconds")) or 0.0
        end = finite_number(block[-1].get("elapsedSeconds")) or start
        eligible = [
            sample
            for sample in block
            if (finite_number(sample.get("elapsedSeconds")) or 0.0) - start >= settle_seconds
        ]
        eligible_start = (
            finite_number(eligible[0].get("elapsedSeconds")) if eligible else None
        )
        eligible_end = (
            finite_number(eligible[-1].get("elapsedSeconds")) if eligible else None
        )
        results.append(
            {
                "frequencyMHz": stable_signature(block[-1], tolerance_mhz)[1],
                "stateAtEnd": (block[-1].get("governor") or {}).get("state"),
                "startElapsedSeconds": round_value(start, 2),
                "endElapsedSeconds": round_value(end, 2),
                "rawDurationSeconds": round_value(max(0.0, end - start), 2),
                "rawSamples": len(block),
                "settleDiscardSeconds": settle_seconds,
                "eligibleDurationSeconds": round_value(
                    max(0.0, eligible_end - eligible_start)
                    if eligible_start is not None and eligible_end is not None
                    else 0.0,
                    2,
                ),
                "eligibleSamples": eligible,
            }
        )
    return results


def transition_summary(samples):
    transitions = []
    previous = None
    for sample in samples:
        governor = sample.get("governor") or {}
        current = (
            governor.get("state"),
            governor.get("lastReason"),
            governor.get("targetFrequencyMHz"),
            governor.get("lastStableFrequencyMHz"),
        )
        if current != previous:
            transitions.append(
                {
                    "elapsedSeconds": sample.get("elapsedSeconds"),
                    "state": current[0],
                    "reason": current[1],
                    "targetFrequencyMHz": current[2],
                    "lastStableFrequencyMHz": current[3],
                }
            )
            previous = current
    return transitions


def summarize_window(samples):
    if not samples:
        return {"samples": 0}
    field_map = {
        "hashrateGhs": "hashrateGhs",
        "hashrate1mGhs": "hashrate1mGhs",
        "hashrate10mGhs": "hashrate10mGhs",
        "theoreticalHashrateGhs": "theoreticalHashrateGhs",
        "calculatedUtilization": "calculatedUtilization",
        "firmwareGovernorUtilization": ("governor", "utilization"),
        "actualFrequencyMHz": "actualFrequencyMHz",
        "powerWatts": "powerWatts",
        "efficiencyJPerTh": "efficiencyJPerTh",
        "inputVoltageVolts": "inputVoltageVolts",
        "currentAmps": "currentAmps",
        "actualCoreVoltageVolts": "actualCoreVoltageVolts",
        "asicTemperatureC": "asicTemperatureC",
        "externalVrTemperatureC": "externalVrTemperatureC",
        "internalVrTemperatureC": "internalVrTemperatureC",
        "normalizedVrTemperatureC": "normalizedVrTemperatureC",
        "activePoolPingMilliseconds": "activePoolPingMilliseconds",
        "activePoolPingLoss": "activePoolPingLoss",
    }
    result = {
        "samples": len(samples),
        "startElapsedSeconds": samples[0].get("elapsedSeconds"),
        "endElapsedSeconds": samples[-1].get("elapsedSeconds"),
    }
    for output_key, path in field_map.items():
        if isinstance(path, tuple):
            values = [(sample.get(path[0]) or {}).get(path[1]) for sample in samples]
        else:
            values = [sample.get(path) for sample in samples]
        result[output_key] = series_stats(values)
    real_mean = scalar(result.get("hashrateGhs"), "mean")
    power_mean = scalar(result.get("powerWatts"), "mean")
    result["aggregateEfficiencyJPerTh"] = round_value(
        power_mean / (real_mean / 1000.0) if power_mean and real_mean else None
    )
    per_chip = []
    chip_count = max((len(sample.get("chipHashratesGhs") or []) for sample in samples), default=0)
    for chip_index in range(chip_count):
        values = [
            sample.get("chipHashratesGhs", [])[chip_index]
            for sample in samples
            if len(sample.get("chipHashratesGhs") or []) > chip_index
        ]
        per_chip.append(series_stats(values))
    result["perChipHashrateGhs"] = per_chip
    result["governorStateCounts"] = dict(
        collections.Counter((sample.get("governor") or {}).get("state") for sample in samples)
    )
    result["governorReasonCounts"] = dict(
        collections.Counter((sample.get("governor") or {}).get("lastReason") for sample in samples)
    )
    result["operationalFraction"] = round_value(
        sum(is_operational(sample) for sample in samples) / len(samples), 6
    )
    return result


def summarize_collection(document, settle_seconds=60.0, tolerance_mhz=0.8):
    samples = document.get("samples") or []
    failures = document.get("requestFailures") or []
    interval = finite_number(scalar(document.get("collection"), "intervalSeconds")) or 5.0
    blocks = find_stable_blocks(samples, interval, settle_seconds, tolerance_mhz)
    qualified = [block for block in blocks if block["eligibleSamples"]]
    primary = max(
        qualified,
        key=lambda block: (
            len(block["eligibleSamples"]),
            block["endElapsedSeconds"],
        ),
        default=None,
    )
    all_stable = []
    for block in blocks:
        entry = {key: value for key, value in block.items() if key != "eligibleSamples"}
        entry["summary"] = summarize_window(block["eligibleSamples"])
        all_stable.append(entry)

    counters = {
        name: counter_delta(samples, key)
        for name, key in (
            ("acceptedShares", "sharesAccepted"),
            ("rejectedShares", "sharesRejected"),
            ("duplicateHardwareNonces", "duplicateHardwareNonces"),
            ("shareQueueDrops", "shareQueueDrops"),
        )
    }
    elapsed = [finite_number(sample.get("elapsedSeconds")) for sample in samples]
    elapsed = [value for value in elapsed if value is not None]
    expected_samples = (
        int(math.floor((max(elapsed) if elapsed else 0.0) / interval)) + 1
        if interval > 0
        else len(samples)
    )
    uptimes = [finite_number(sample.get("uptimeSeconds")) for sample in samples]
    uptimes = [value for value in uptimes if value is not None]
    reboot_detected = any(current < previous for previous, current in zip(uptimes, uptimes[1:]))
    board_errors = sorted(
        {sample.get("boardError") for sample in samples if sample.get("boardError") not in (0, None)},
        key=str,
    )
    shutdown_samples = sum(bool(sample.get("shutdown")) for sample in samples)
    pool_connected_fraction = (
        sum(bool(sample.get("activePoolConnected")) for sample in samples) / len(samples)
        if samples
        else 0.0
    )
    final_governor = (samples[-1].get("governor") or {}) if samples else {}
    summary = {
        "sampleCount": len(samples),
        "requestFailureCount": len(failures),
        "expectedSampleCountApproximate": expected_samples,
        "coverageFraction": round_value(len(samples) / expected_samples if expected_samples else 0.0, 6),
        "durationSeconds": round_value(max(elapsed) - min(elapsed), 2) if elapsed else 0.0,
        "rebootDetected": reboot_detected,
        "boardErrors": board_errors,
        "shutdownSamples": shutdown_samples,
        "poolConnectedFraction": round_value(pool_connected_fraction, 6),
        "counterDeltas": counters,
        "governorTransitions": transition_summary(samples),
        "finalGovernor": final_governor,
        "allSamples": summarize_window(samples),
        "stableBlocks": all_stable,
        "primaryStableBlock": None,
    }
    if primary:
        summary["primaryStableBlock"] = {
            key: value for key, value in primary.items() if key != "eligibleSamples"
        }
        summary["primaryStableBlock"]["summary"] = summarize_window(primary["eligibleSamples"])
    return summary


def legacy_baseline_reference(document):
    snapshot = document.get("prePatchSnapshot") or {}
    history = document.get("threeHourHistory") or {}
    asic_count = finite_number(scalar(document.get("device"), "asicCount"))
    core_count = finite_number(scalar(document.get("device"), "smallCoreCount"))
    frequency = finite_number(snapshot.get("actualFrequencyMHz"))
    theoretical = asic_count * core_count * frequency / 1000.0 if asic_count and core_count and frequency else None
    return {
        "sourceType": "legacy-three-hour-summary-plus-point-snapshot",
        "note": (
            "The three-hour history has no per-sample frequency/power. Empirical deltas use "
            "the pre-patch 525 MHz snapshot; the history is context only, not a strict A/B window."
        ),
        "frequencyMHz": frequency,
        "hashrateGhs": finite_number(snapshot.get("hashrateGhs")),
        "hashrate1mGhs": finite_number(snapshot.get("hashrate1mGhs")),
        "hashrate10mGhs": finite_number(snapshot.get("hashrate10mGhs")),
        "theoreticalHashrateGhs": theoretical,
        "calculatedUtilization": (
            finite_number(snapshot.get("hashrateGhs")) / theoretical
            if theoretical and finite_number(snapshot.get("hashrateGhs")) is not None
            else None
        ),
        "powerWatts": finite_number(snapshot.get("powerWatts")),
        "efficiencyJPerTh": finite_number(snapshot.get("efficiencyJPerTh")),
        "lifetimeRejectedSharePercent": finite_number(snapshot.get("rejectedSharePercent")),
        "lifetimeSharesAccepted": snapshot.get("sharesAccepted"),
        "lifetimeSharesRejected": snapshot.get("sharesRejected"),
        "lifetimeDuplicateHardwareNonces": snapshot.get("duplicateHardwareNonces"),
        "lifetimeShareQueueDrops": snapshot.get("shareQueueDrops"),
        "asicTemperatureC": finite_number(snapshot.get("asicTemperatureC")),
        "externalVrTemperatureC": finite_number(snapshot.get("externalVrTemperatureC")),
        "normalizedVrTemperatureC": finite_number(snapshot.get("normalizedVrTemperatureC")),
        "historyHashrate10mGhs": history.get("hashrate10mGhs"),
        "historyAsicTemperatureC": history.get("asicTemperatureC"),
        "historyExternalVrTemperatureC": history.get("externalVrTemperatureC"),
    }


def percent_change(after, before):
    after = finite_number(after)
    before = finite_number(before)
    if after is None or before in (None, 0.0):
        return None
    return 100.0 * (after / before - 1.0)


def compare_to_baseline(summary, baseline_document):
    baseline = legacy_baseline_reference(baseline_document)
    primary = scalar(summary.get("primaryStableBlock"), "summary", {})
    actual_frequency = scalar(primary.get("actualFrequencyMHz"), "mean")
    real_hashrate = scalar(primary.get("hashrateGhs"), "trimmedMean")
    theoretical = scalar(primary.get("theoreticalHashrateGhs"), "mean")
    power = scalar(primary.get("powerWatts"), "mean")
    efficiency = primary.get("aggregateEfficiencyJPerTh")
    asic_temp = scalar(primary.get("asicTemperatureC"), "max")
    vr_temp = scalar(primary.get("normalizedVrTemperatureC"), "max")
    expected_gain = percent_change(theoretical, baseline.get("theoreticalHashrateGhs"))
    empirical_gain = percent_change(real_hashrate, baseline.get("hashrateGhs"))
    capture = (
        empirical_gain / expected_gain
        if expected_gain is not None and expected_gain > 0 and empirical_gain is not None
        else None
    )
    comparison = {
        "baseline": {key: round_value(value) if isinstance(value, (int, float)) else value for key, value in baseline.items()},
        "postStable": {
            "frequencyMHz": round_value(actual_frequency),
            "hashrateGhsTrimmedMean": round_value(real_hashrate),
            "theoreticalHashrateGhsMean": round_value(theoretical),
            "powerWattsMean": round_value(power),
            "efficiencyJPerTh": round_value(efficiency),
            "asicTemperatureCMax": round_value(asic_temp),
            "normalizedVrTemperatureCMax": round_value(vr_temp),
        },
        "deltas": {
            "frequencyPercent": round_value(percent_change(actual_frequency, baseline.get("frequencyMHz"))),
            "empiricalHashratePercent": round_value(empirical_gain),
            "theoreticalHashratePercent": round_value(expected_gain),
            "fractionOfTheoreticalGainCaptured": round_value(capture),
            "powerPercent": round_value(percent_change(power, baseline.get("powerWatts"))),
            "efficiencyPercent": round_value(percent_change(efficiency, baseline.get("efficiencyJPerTh"))),
            "asicTemperatureC": round_value(
                asic_temp - baseline["asicTemperatureC"]
                if asic_temp is not None and baseline.get("asicTemperatureC") is not None
                else None
            ),
            "normalizedVrTemperatureC": round_value(
                vr_temp - baseline["normalizedVrTemperatureC"]
                if vr_temp is not None and baseline.get("normalizedVrTemperatureC") is not None
                else None
            ),
        },
    }
    comparison["acceptance"] = evaluate_acceptance(summary, comparison)
    return comparison


def evaluate_acceptance(summary, comparison):
    primary_block = summary.get("primaryStableBlock") or {}
    primary = primary_block.get("summary") or {}
    deltas = comparison.get("deltas") or {}
    counters = summary.get("counterDeltas") or {}
    final = summary.get("finalGovernor") or {}
    expected_gain = finite_number(deltas.get("theoreticalHashratePercent"))
    empirical_gain = finite_number(deltas.get("empiricalHashratePercent"))
    minimum_gain = max(0.5, expected_gain - 0.75) if expected_gain is not None and expected_gain > 0 else 0.0

    checks = []

    def add(name, passed, observed, threshold, hard=True, note=None):
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "hardGate": hard,
                "observed": observed,
                "threshold": threshold,
                "note": note,
            }
        )

    stable_duration = finite_number(primary_block.get("eligibleDurationSeconds")) or 0.0
    add("stable-window-duration", stable_duration >= 300.0, stable_duration, ">= 300 s")
    add("request-coverage", (finite_number(summary.get("coverageFraction")) or 0.0) >= 0.98,
        summary.get("coverageFraction"), ">= 0.98")
    add("no-reboot", not summary.get("rebootDetected"), summary.get("rebootDetected"), "false")
    add("no-board-error-or-shutdown", not summary.get("boardErrors") and summary.get("shutdownSamples") == 0,
        {"boardErrors": summary.get("boardErrors"), "shutdownSamples": summary.get("shutdownSamples")}, "none")
    add("pool-connectivity", (finite_number(summary.get("poolConnectedFraction")) or 0.0) >= 0.99,
        summary.get("poolConnectedFraction"), ">= 0.99")
    add("calculated-utilization-median", (finite_number(scalar(primary.get("calculatedUtilization"), "p50")) or 0.0) >= 0.985,
        scalar(primary.get("calculatedUtilization"), "p50"), ">= 0.985")
    add("calculated-utilization-p05", (finite_number(scalar(primary.get("calculatedUtilization"), "p05")) or 0.0) >= 0.975,
        scalar(primary.get("calculatedUtilization"), "p05"), ">= 0.975")
    add("frequency-increased", expected_gain is not None and expected_gain > 0.0,
        expected_gain, "theoretical gain > 0%")
    add("empirical-hashrate-gain", empirical_gain is not None and empirical_gain >= minimum_gain,
        empirical_gain, ">= {:.3f}%".format(minimum_gain),
        note="Baseline is one 525 MHz point; normalized utilization is the stronger short-run signal.")
    add("power-p95", (finite_number(scalar(primary.get("powerWatts"), "p95")) or math.inf) < 69.0,
        scalar(primary.get("powerWatts"), "p95"), "< 69.0 W")
    add("power-absolute-max", (finite_number(scalar(primary.get("powerWatts"), "max")) or math.inf) < 70.0,
        scalar(primary.get("powerWatts"), "max"), "< 70.0 W")
    add("current-p95", (finite_number(scalar(primary.get("currentAmps"), "p95")) or math.inf) < 5.9,
        scalar(primary.get("currentAmps"), "p95"), "< 5.9 A")
    add("asic-temperature-max", (finite_number(scalar(primary.get("asicTemperatureC"), "max")) or math.inf) < 65.0,
        scalar(primary.get("asicTemperatureC"), "max"), "< 65.0 C")
    add("normalized-vr-temperature-max", (finite_number(scalar(primary.get("normalizedVrTemperatureC"), "max")) or math.inf) < 75.0,
        scalar(primary.get("normalizedVrTemperatureC"), "max"), "< 75.0 C")
    for counter_name in ("rejectedShares", "duplicateHardwareNonces", "shareQueueDrops"):
        delta = scalar(counters.get(counter_name), "delta")
        add("no-new-{}".format(counter_name), delta == 0, delta, "= 0")
    efficiency_delta = finite_number(deltas.get("efficiencyPercent"))
    add("efficiency-regression", efficiency_delta is not None and efficiency_delta <= 3.0,
        efficiency_delta, "<= +3.0%")
    final_target = finite_number(final.get("targetFrequencyMHz"))
    final_stable = finite_number(final.get("lastStableFrequencyMHz"))
    final_reason = final.get("lastReason")
    final_state = final.get("state")
    add("governor-final-state", final_target is not None and final_target == final_stable and
        final_state in SAFE_GOVERNOR_STATES and final_reason not in UNSAFE_FINAL_REASONS,
        final, "target=stable; state observe/cooldown; no unsafe reason")

    hard = [check for check in checks if check["hardGate"]]
    return {
        "passed": bool(hard) and all(check["passed"] for check in hard),
        "checks": checks,
        "shareRateCaveat": (
            "A 10-20 minute window may contain too few pool shares for a statistically useful "
            "reject percentage. Counter increments are treated as a safety signal, not a rate estimate."
        ),
    }


def atomic_json_write(path, document, overwrite=False):
    path = pathlib.Path(os.path.abspath(os.path.expanduser(str(path))))
    if path.is_symlink():
        raise FileExistsError("refusing to replace symlink: {}".format(path))
    if path.exists() and not overwrite:
        raise FileExistsError("output already exists (use --force): {}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent), prefix=path.name + ".", delete=False
        ) as handle:
            json.dump(document, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            temporary = pathlib.Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def collect(args):
    parsed = urllib.parse.urlsplit(args.url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SystemExit("URL must include http:// or https:// and a hostname")
    base_url = args.url.rstrip("/") + "/"
    identify = request_json(base_url, "/api/v2/identify", args.timeout)
    system = request_json(base_url, "/api/v2/system", args.timeout)
    settings = request_json(base_url, "/api/v2/settings", args.timeout)
    metadata = sanitized_metadata(identify, system, settings)
    if metadata.get("deviceModel") != "NerdQAxe+":
        raise SystemExit("Refusing benchmark: expected NerdQAxe+, got {!r}".format(metadata.get("deviceModel")))

    samples = []
    failures = []
    started_wall = utc_now()
    started = time.monotonic()
    next_due = started
    while True:
        now = time.monotonic()
        if now < next_due:
            time.sleep(next_due - now)
        elapsed = time.monotonic() - started
        try:
            dashboard = request_json(base_url, "/api/v2/dashboard", args.timeout)
            samples.append(sanitize_dashboard(dashboard, elapsed))
            print(
                "\r{:6.1f}s  {:7.1f} GH/s  {:5.2f} W  {:6.1f} MHz  {:>8s}".format(
                    elapsed,
                    samples[-1].get("hashrateGhs") or 0.0,
                    samples[-1].get("powerWatts") or 0.0,
                    samples[-1].get("actualFrequencyMHz") or 0.0,
                    str((samples[-1].get("governor") or {}).get("state") or "?"),
                ),
                end="",
                flush=True,
            )
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            failures.append(
                {
                    "capturedUtc": utc_now(),
                    "elapsedSeconds": round_value(elapsed, 3),
                    "errorType": type(exc).__name__,
                    "message": str(exc)[:200],
                }
            )
            print("\r{:6.1f}s  request failed: {}".format(elapsed, type(exc).__name__), end="", flush=True)
        if elapsed >= args.duration:
            break
        next_due += args.interval
        if next_due <= time.monotonic():
            next_due = time.monotonic() + args.interval
    print()

    document = {
        "schema": SCHEMA,
        "createdUtc": utc_now(),
        "label": args.label,
        "device": metadata,
        "collection": {
            "startedUtc": started_wall,
            "endedUtc": utc_now(),
            "requestedDurationSeconds": args.duration,
            "intervalSeconds": args.interval,
            "requestTimeoutSeconds": args.timeout,
            "readOnlyEndpoints": [
                "/api/v2/identify",
                "/api/v2/system",
                "/api/v2/settings",
                "/api/v2/dashboard",
            ],
            "sensitiveFieldsPersisted": False,
        },
        "samples": samples,
        "requestFailures": failures,
    }
    document["summary"] = summarize_collection(
        document, settle_seconds=args.settle_seconds, tolerance_mhz=args.frequency_tolerance
    )
    if args.baseline:
        with args.baseline.open("r", encoding="utf-8") as handle:
            baseline_document = json.load(handle)
        document["comparisonToBaseline"] = compare_to_baseline(document["summary"], baseline_document)
    atomic_json_write(args.output, document, overwrite=args.force)
    print("Saved {} samples to {}".format(len(samples), args.output.resolve()))
    if args.baseline:
        acceptance = document["comparisonToBaseline"]["acceptance"]
        print("Acceptance: {}".format("PASS" if acceptance["passed"] else "FAIL / needs review"))


def resummarize(args):
    with args.input.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if document.get("schema") != SCHEMA:
        raise SystemExit("Input is not a {} collection".format(SCHEMA))
    document["summary"] = summarize_collection(
        document, settle_seconds=args.settle_seconds, tolerance_mhz=args.frequency_tolerance
    )
    if args.baseline:
        with args.baseline.open("r", encoding="utf-8") as handle:
            baseline_document = json.load(handle)
        document["comparisonToBaseline"] = compare_to_baseline(document["summary"], baseline_document)
    output = args.output or args.input.with_name(
        "{}-summarized{}".format(args.input.stem, args.input.suffix)
    )
    atomic_json_write(output, document, overwrite=args.force)
    print("Updated summary in {}".format(output.resolve()))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect", help="collect GET-only telemetry")
    collect_parser.add_argument(
        "--url", default=DEFAULT_URL,
        help="device base URL (default: NERDQAXE_URL or %(default)s)",
    )
    collect_parser.add_argument("--duration", type=float, default=1200.0,
                                help="collection seconds (default: 1200 / 20 min)")
    collect_parser.add_argument("--interval", type=float, default=5.0)
    collect_parser.add_argument("--timeout", type=float, default=3.0)
    collect_parser.add_argument("--settle-seconds", type=float, default=60.0,
                                help="discard this much from each new stable block")
    collect_parser.add_argument("--frequency-tolerance", type=float, default=0.8)
    collect_parser.add_argument("--label", default="post-flash")
    collect_parser.add_argument("--baseline", type=pathlib.Path)
    collect_parser.add_argument("--output", type=pathlib.Path,
                                default=default_output_path())
    collect_parser.add_argument("--force", action="store_true",
                                help="replace an existing regular output file")
    collect_parser.set_defaults(func=collect)

    summary_parser = subparsers.add_parser("summarize", help="rebuild a saved summary")
    summary_parser.add_argument("input", type=pathlib.Path)
    summary_parser.add_argument("--baseline", type=pathlib.Path)
    summary_parser.add_argument("--output", type=pathlib.Path)
    summary_parser.add_argument("--settle-seconds", type=float, default=60.0)
    summary_parser.add_argument("--frequency-tolerance", type=float, default=0.8)
    summary_parser.add_argument("--force", action="store_true",
                                help="replace an existing regular output file")
    summary_parser.set_defaults(func=resummarize)
    return parser


def main():
    args = build_parser().parse_args()
    numeric_rules = [
        ("settle-seconds", args.settle_seconds, 0.0, True),
        ("frequency-tolerance", args.frequency_tolerance, 0.0, True),
    ]
    if args.command == "collect":
        numeric_rules.extend(
            [
                ("duration", args.duration, 0.0, True),
                ("interval", args.interval, MIN_INTERVAL_SECONDS, True),
                ("timeout", args.timeout, 0.0, False),
            ]
        )
    for name, value, minimum, inclusive in numeric_rules:
        valid = math.isfinite(value) and (value >= minimum if inclusive else value > minimum)
        if not valid:
            operator = ">=" if inclusive else ">"
            raise SystemExit("{} must be finite and {} {}".format(name, operator, minimum))
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except FileExistsError as error:
        print("error: {}".format(error), file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nInterrupted before a complete output could be written", file=sys.stderr)
        raise SystemExit(130)
