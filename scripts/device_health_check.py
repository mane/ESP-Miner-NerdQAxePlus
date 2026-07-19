#!/usr/bin/env python3
"""Read-only health check for a NerdQAxe miner HTTP API."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Finding:
    level: str
    message: str


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def fetch_json(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "nerdqaxe-health-check/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        data = json.load(response)
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} did not return a JSON object")
    return data


def evaluate_health(
    identify: dict[str, Any],
    system: dict[str, Any],
    dashboard: dict[str, Any],
    settings: dict[str, Any],
    expected_model: str | None = None,
    expected_version: str | None = None,
) -> tuple[dict[str, Any], list[Finding]]:
    findings: list[Finding] = []
    model = str(system.get("deviceModel") or identify.get("deviceModel") or "unknown")
    asic = str(system.get("asicModel") or settings.get("asicModel") or "unknown")
    version = str(system.get("version") or "unknown")

    if expected_model and model != expected_model:
        findings.append(Finding("ERROR", f"expected device model {expected_model!r}, got {model!r}"))
    if expected_version and version != expected_version:
        findings.append(Finding("ERROR", f"expected firmware version {expected_version!r}, got {version!r}"))

    system_state = dashboard.get("system") if isinstance(dashboard.get("system"), dict) else {}
    if bool(system_state.get("shutdown")):
        findings.append(Finding("ERROR", "ASIC power is shut down"))
    board_error = int(_number(system_state.get("boardError")))
    if board_error:
        findings.append(Finding("ERROR", f"board error code is {board_error}"))

    memory = system.get("memory") if isinstance(system.get("memory"), dict) else {}
    free_internal = int(_number(memory.get("freeHeapInt")))
    if free_internal and free_internal < 64 * 1024:
        findings.append(Finding("WARN", f"internal free heap is low ({free_internal} bytes)"))

    performance = dashboard.get("performance") if isinstance(dashboard.get("performance"), dict) else {}
    hash_rate = _number(performance.get("hashRate"))
    if hash_rate <= 0:
        findings.append(Finding("ERROR", "hash rate is zero"))

    power = dashboard.get("power") if isinstance(dashboard.get("power"), dict) else {}
    watts = _number(power.get("watts"))
    efficiency = (watts * 1000.0 / hash_rate) if watts > 0 and hash_rate > 0 else None

    thermal = dashboard.get("thermal") if isinstance(dashboard.get("thermal"), dict) else {}
    asic_temp = _number(thermal.get("asicTemp"))
    vr_temp = _number(thermal.get("vrTemp"))
    overheat_temp = _number(system_state.get("overheatTemp"))
    if overheat_temp and asic_temp >= overheat_temp:
        findings.append(
            Finding("ERROR", f"ASIC temperature {asic_temp:.1f} C reached its {overheat_temp:.1f} C limit")
        )

    stratum = dashboard.get("stratum") if isinstance(dashboard.get("stratum"), dict) else {}
    pools = stratum.get("pools") if isinstance(stratum.get("pools"), list) else []
    if not any(isinstance(pool, dict) and pool.get("connected") is True for pool in pools):
        findings.append(Finding("ERROR", "no Stratum pool is connected"))

    fan_settings = settings.get("fans") if isinstance(settings.get("fans"), list) else []
    fans = thermal.get("fans") if isinstance(thermal.get("fans"), list) else []
    fan_summary: list[dict[str, Any]] = []
    for index, fan in enumerate(fans):
        if not isinstance(fan, dict):
            continue
        label = f"fan {index + 1}"
        if index < len(fan_settings) and isinstance(fan_settings[index], dict):
            label = str(fan_settings[index].get("label") or label)
        speed = _number(fan.get("speed"))
        rpm = int(_number(fan.get("rpm")))
        fan_summary.append({"label": label, "speedPercent": speed, "rpm": rpm})
        if speed >= 30 and rpm == 0:
            findings.append(Finding("WARN", f"{label} is commanded at {speed:.0f}% but reports 0 RPM"))

    summary = {
        "healthy": not any(item.level == "ERROR" for item in findings),
        "deviceModel": model,
        "asicModel": asic,
        "version": version,
        "uptimeSeconds": int(_number(system.get("uptimeSeconds"))),
        "hashRateGh": hash_rate,
        "powerWatts": watts,
        "efficiencyJPerTh": efficiency,
        "asicTempC": asic_temp,
        "vrTempC": vr_temp,
        "freeHeap": int(_number(memory.get("freeHeap"))),
        "freeHeapInternal": free_internal,
        "fans": fan_summary,
    }
    return summary, findings


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="device base URL, for example http://192.168.1.42/")
    parser.add_argument("--expect-model", help="fail if the reported device model differs")
    parser.add_argument("--expect-version", help="fail if the reported firmware version differs")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds (default: 5)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        identify = fetch_json(args.url, "/api/v2/identify", args.timeout)
        system = fetch_json(args.url, "/api/v2/system", args.timeout)
        dashboard = fetch_json(args.url, "/api/v2/dashboard", args.timeout)
        settings = fetch_json(args.url, "/api/v2/settings", args.timeout)
        summary, findings = evaluate_health(
            identify,
            system,
            dashboard,
            settings,
            args.expect_model,
            args.expect_version,
        )
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        if args.json:
            print(json.dumps({"healthy": False, "error": str(error)}, separators=(",", ":")))
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if args.json:
        output = dict(summary)
        output["findings"] = [asdict(item) for item in findings]
        print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    else:
        efficiency = summary["efficiencyJPerTh"]
        efficiency_text = f", {efficiency:.2f} J/TH" if efficiency is not None else ""
        print(f"{summary['deviceModel']} / {summary['asicModel']} / {summary['version']}")
        print(
            f"Hashrate {summary['hashRateGh'] / 1000.0:.3f} TH/s, "
            f"power {summary['powerWatts']:.2f} W{efficiency_text}"
        )
        print(f"Temperatures ASIC {summary['asicTempC']:.1f} C, VR {summary['vrTempC']:.1f} C")
        for fan in summary["fans"]:
            print(f"Fan {fan['label']}: {fan['speedPercent']:.0f}% / {fan['rpm']} RPM")
        for finding in findings:
            print(f"{finding.level}: {finding.message}")
        print("HEALTHY" if summary["healthy"] else "UNHEALTHY")

    return 0 if summary["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
