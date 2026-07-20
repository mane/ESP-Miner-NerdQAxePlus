#!/usr/bin/env python3
"""Read-only health check for a NerdQAxe miner HTTP API."""

from __future__ import annotations

import argparse
import ast
import gzip
import http.client
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any


@dataclass(frozen=True)
class Finding:
    level: str
    message: str


@dataclass(frozen=True)
class WebUiBuild:
    version: str | None
    commit: str | None


class _WebUiDocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.script_sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "base" and self.base_href is None and attributes.get("href"):
            self.base_href = attributes["href"]
        elif tag.lower() == "script" and attributes.get("src"):
            self.script_sources.append(attributes["src"])


_MAX_WEB_ASSET_BYTES = 8 * 1024 * 1024
_MAIN_BUNDLE_RE = re.compile(r"main(?:\.[A-Za-z0-9_-]+)*\.js", re.IGNORECASE)


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
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"{path} returned HTTP {response.status}")
            data = json.load(response)
    except http.client.HTTPException as error:
        raise RuntimeError(f"{path} response could not be read: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} did not return a JSON object")
    return data


def _cache_busted(url: str, token: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.append(("_health", token))
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )


def _same_origin(left: str, right: str) -> bool:
    left_parts = urllib.parse.urlsplit(left)
    right_parts = urllib.parse.urlsplit(right)

    def normalized_port(parts: urllib.parse.SplitResult) -> int | None:
        if parts.port is not None:
            return parts.port
        return {"http": 80, "https": 443}.get(parts.scheme.lower())

    return (
        left_parts.scheme.lower(),
        left_parts.hostname,
        normalized_port(left_parts),
    ) == (
        right_parts.scheme.lower(),
        right_parts.hostname,
        normalized_port(right_parts),
    )


def _read_web_text(url: str, timeout: float) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,*/*;q=0.1",
            "Accept-Encoding": "gzip",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "nerdqaxe-health-check/1",
        },
    )
    path = urllib.parse.urlsplit(url).path or "/"
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"{path} returned HTTP {response.status}")
            payload = response.read(_MAX_WEB_ASSET_BYTES + 1)
            effective_url = response.geturl()
            content_encoding = str(response.headers.get("Content-Encoding") or "").lower()
    except http.client.HTTPException as error:
        raise RuntimeError(f"{path} response could not be read: {error}") from error

    if len(payload) > _MAX_WEB_ASSET_BYTES:
        raise RuntimeError(f"Web UI asset exceeds {_MAX_WEB_ASSET_BYTES} bytes")
    if payload.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                payload = compressed.read(_MAX_WEB_ASSET_BYTES + 1)
        except (EOFError, OSError, zlib.error) as error:
            raise RuntimeError(f"{path} contains invalid gzip data: {error}") from error
        if len(payload) > _MAX_WEB_ASSET_BYTES:
            raise RuntimeError(f"decompressed Web UI asset exceeds {_MAX_WEB_ASSET_BYTES} bytes")
    elif content_encoding and content_encoding not in ("identity", "gzip"):
        raise RuntimeError(f"unsupported Web UI content encoding {content_encoding!r}")

    try:
        return payload.decode("utf-8-sig"), effective_url
    except UnicodeDecodeError as error:
        raise RuntimeError("Web UI asset is not valid UTF-8") from error


def _extract_js_marker(bundle: str, marker: str) -> str | None:
    # Keep the declaration and marker use in the same lexical block. Production
    # minifiers reuse short identifiers in adjacent functions, so a free search
    # after each assignment can otherwise bind VERSION/COMMIT to another scope.
    marker_pattern = re.compile(
        r"\b(?:const|let|var)\s+"
        r"(?P<variable>[A-Za-z_$][\w$]*)\s*=\s*"
        r"(?P<literal>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
        r"(?P<tail>[^{}]{0,512}?)"
        r"(?<![\w$])(?P=variable)\.includes\(\s*['\"]"
        + re.escape(marker)
        + r"['\"]\s*\)"
    )
    for match in marker_pattern.finditer(bundle):
        literal = match.group("literal")
        try:
            value = ast.literal_eval(literal)
        except (SyntaxError, ValueError):
            continue
        if not isinstance(value, str):
            continue
        value = value.strip()
        if (not value or value in (marker, f"__{marker}__") or
                len(value) > 128 or not value.isprintable()):
            continue
        return value
    return None


def extract_web_ui_build(bundle: str) -> WebUiBuild:
    return WebUiBuild(
        version=_extract_js_marker(bundle, "VERSION"),
        commit=_extract_js_marker(bundle, "COMMIT"),
    )


def fetch_web_ui_build(
    base_url: str,
    timeout: float,
    cache_token: str | None = None,
) -> WebUiBuild:
    token = cache_token or f"{time.time_ns():x}"
    index_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", "./")
    index, effective_index_url = _read_web_text(_cache_busted(index_url, f"{token}-index"), timeout)
    if not _same_origin(index_url, effective_index_url):
        raise RuntimeError("Web UI index redirected outside the device origin")

    parser = _WebUiDocumentParser()
    parser.feed(index)
    document_base = urllib.parse.urljoin(effective_index_url, parser.base_href or "")
    bundle_url: str | None = None
    for source in parser.script_sources:
        candidate = urllib.parse.urljoin(document_base, source)
        filename = urllib.parse.urlsplit(candidate).path.rsplit("/", 1)[-1]
        if _MAIN_BUNDLE_RE.fullmatch(filename) and _same_origin(effective_index_url, candidate):
            bundle_url = candidate
            break
    if bundle_url is None:
        raise RuntimeError("Web UI index does not reference a same-origin main JavaScript bundle")

    bundle, effective_bundle_url = _read_web_text(
        _cache_busted(bundle_url, f"{token}-main"), timeout
    )
    if not _same_origin(index_url, effective_bundle_url):
        raise RuntimeError("Web UI main bundle redirected outside the device origin")
    return extract_web_ui_build(bundle)


def evaluate_health(
    identify: dict[str, Any],
    system: dict[str, Any],
    dashboard: dict[str, Any],
    settings: dict[str, Any],
    expected_model: str | None = None,
    expected_version: str | None = None,
    web_ui_build: WebUiBuild | None = None,
    web_ui_error: str | None = None,
) -> tuple[dict[str, Any], list[Finding]]:
    findings: list[Finding] = []
    model = str(system.get("deviceModel") or identify.get("deviceModel") or "unknown")
    asic = str(system.get("asicModel") or settings.get("asicModel") or "unknown")
    version = str(system.get("version") or "unknown")

    if expected_model and model != expected_model:
        findings.append(Finding("ERROR", f"expected device model {expected_model!r}, got {model!r}"))
    if expected_version and version != expected_version:
        findings.append(Finding("ERROR", f"expected firmware version {expected_version!r}, got {version!r}"))

    if web_ui_error:
        findings.append(Finding("ERROR", f"Web UI could not be verified: {web_ui_error}"))
    elif web_ui_build is not None:
        missing_markers: list[str] = []
        if web_ui_build.version:
            if version == "unknown":
                findings.append(Finding("WARN", "firmware version is unknown; Web UI version cannot be compared"))
            elif web_ui_build.version != version:
                findings.append(
                    Finding(
                        "ERROR",
                        f"Web UI version {web_ui_build.version!r} does not match firmware version {version!r}",
                    )
                )
        else:
            missing_markers.append("version")
        if not web_ui_build.commit:
            missing_markers.append("commit")
        if missing_markers:
            findings.append(
                Finding("WARN", f"Web UI {' and '.join(missing_markers)} marker(s) could not be determined")
            )

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
    configured_frequency = _number(performance.get("configuredFrequency"), _number(settings.get("frequency")))
    effective_frequency = _number(performance.get("frequency"), _number(settings.get("effectiveFrequency")))
    actual_frequency = _number(performance.get("actualFrequency"), effective_frequency)
    rejected_shares = int(_number(performance.get("sharesRejected")))
    duplicate_nonces = int(_number(performance.get("duplicateHWNonces")))
    share_queue_drops = int(_number(performance.get("shareQueueDrops")))
    if share_queue_drops:
        findings.append(Finding("WARN", f"share submission queue dropped {share_queue_drops} result(s)"))

    governor = performance.get("hashrateGovernor")
    if not isinstance(governor, dict):
        governor = settings.get("hashrateGovernor") if isinstance(settings.get("hashrateGovernor"), dict) else {}

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
        "webUiVersion": web_ui_build.version if web_ui_build else None,
        "webUiCommit": web_ui_build.commit if web_ui_build else None,
        "uptimeSeconds": int(_number(system.get("uptimeSeconds"))),
        "hashRateGh": hash_rate,
        "configuredFrequencyMhz": configured_frequency,
        "effectiveFrequencyMhz": effective_frequency,
        "actualFrequencyMhz": actual_frequency,
        "sharesRejected": rejected_shares,
        "duplicateHardwareNonces": duplicate_nonces,
        "shareQueueDrops": share_queue_drops,
        "hashrateGovernor": {
            "enabled": bool(governor.get("enabled")),
            "state": str(governor.get("state") or "unknown"),
            "reason": str(governor.get("lastReason") or "unknown"),
            "targetFrequencyMhz": _number(governor.get("targetFrequency")),
            "lastStableFrequencyMhz": _number(governor.get("lastStableFrequency")),
            "utilization": _number(governor.get("utilization")),
        },
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
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="skip Web UI verification and check only the JSON API",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        identify = fetch_json(args.url, "/api/v2/identify", args.timeout)
        system = fetch_json(args.url, "/api/v2/system", args.timeout)
        dashboard = fetch_json(args.url, "/api/v2/dashboard", args.timeout)
        settings = fetch_json(args.url, "/api/v2/settings", args.timeout)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError, http.client.HTTPException) as error:
        if args.json:
            print(json.dumps({"healthy": False, "error": str(error)}, separators=(",", ":")))
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    web_ui_build: WebUiBuild | None = None
    web_ui_error: str | None = None
    if not args.api_only:
        try:
            web_ui_build = fetch_web_ui_build(args.url, args.timeout)
        except (
            EOFError,
            OSError,
            ValueError,
            RuntimeError,
            urllib.error.URLError,
            http.client.HTTPException,
            zlib.error,
        ) as error:
            web_ui_error = str(error)

    summary, findings = evaluate_health(
        identify,
        system,
        dashboard,
        settings,
        args.expect_model,
        args.expect_version,
        web_ui_build,
        web_ui_error,
    )

    if args.json:
        output = dict(summary)
        output["findings"] = [asdict(item) for item in findings]
        print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    else:
        efficiency = summary["efficiencyJPerTh"]
        efficiency_text = f", {efficiency:.2f} J/TH" if efficiency is not None else ""
        print(f"{summary['deviceModel']} / {summary['asicModel']} / {summary['version']}")
        if args.api_only:
            print("Web UI check skipped (--api-only)")
        else:
            print(
                f"Web UI {summary['webUiVersion'] or 'unknown'} / "
                f"commit {summary['webUiCommit'] or 'unknown'}"
            )
        print(
            f"Hashrate {summary['hashRateGh'] / 1000.0:.3f} TH/s, "
            f"power {summary['powerWatts']:.2f} W{efficiency_text}"
        )
        print(
            f"Frequency {summary['effectiveFrequencyMhz']:.2f} MHz effective "
            f"({summary['configuredFrequencyMhz']:.0f} MHz base, "
            f"{summary['actualFrequencyMhz']:.2f} MHz PLL)"
        )
        governor = summary["hashrateGovernor"]
        print(
            f"Governor {'enabled' if governor['enabled'] else 'disabled'}: "
            f"{governor['state']} / {governor['reason']}, "
            f"target {governor['targetFrequencyMhz']:.0f} MHz, "
            f"utilization {governor['utilization']:.3f}"
        )
        print(
            f"Shares rejected {summary['sharesRejected']}, duplicate nonces "
            f"{summary['duplicateHardwareNonces']}, queue drops {summary['shareQueueDrops']}"
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
