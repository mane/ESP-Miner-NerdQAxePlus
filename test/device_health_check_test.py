import contextlib
import gzip
import http.client
import importlib.util
import io
import json
import pathlib
import sys
import unittest
import urllib.parse
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "device_health_check", ROOT / "scripts" / "device_health_check.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeResponse:
    def __init__(self, url, payload, headers=None, status=200):
        self._url = url
        self._payload = payload
        self.headers = headers or {}
        self.status = status

    def read(self, size=-1):
        return self._payload if size < 0 else self._payload[:size]

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class DeviceHealthCheckTest(unittest.TestCase):
    def setUp(self):
        self.identify = {"deviceModel": "NerdQAxe+"}
        self.system = {
            "deviceModel": "NerdQAxe+",
            "asicModel": "BM1368",
            "version": "test",
            "uptimeSeconds": 100,
            "memory": {"freeHeap": 1_000_000, "freeHeapInt": 100_000},
        }
        self.dashboard = {
            "system": {"shutdown": False, "boardError": 0, "overheatTemp": 70},
            "performance": {
                "hashRate": 2500,
                "frequency": 525,
                "configuredFrequency": 490,
                "actualFrequency": 524.99,
                "sharesRejected": 0,
                "duplicateHWNonces": 0,
                "shareQueueDrops": 0,
                "hashrateGovernor": {
                    "enabled": True,
                    "state": "observe",
                    "lastReason": "at_frequency_cap",
                    "targetFrequency": 525,
                    "lastStableFrequency": 525,
                    "utilization": 0.998,
                },
            },
            "power": {"watts": 60},
            "thermal": {
                "asicTemp": 55,
                "vrTemp": 65,
                "fans": [{"speed": 80, "rpm": 2500}, {"speed": 80, "rpm": 2200}],
            },
            "stratum": {"pools": [{"connected": True}]},
        }
        self.settings = {"fans": [{"label": "M2"}, {"label": "M1"}]}

    def test_healthy_miner_reports_efficiency_without_errors(self):
        summary, findings = MODULE.evaluate_health(
            self.identify, self.system, self.dashboard, self.settings, "NerdQAxe+"
        )
        self.assertTrue(summary["healthy"])
        self.assertAlmostEqual(summary["efficiencyJPerTh"], 24.0)
        self.assertEqual(summary["configuredFrequencyMhz"], 490)
        self.assertEqual(summary["effectiveFrequencyMhz"], 525)
        self.assertEqual(summary["hashrateGovernor"]["state"], "observe")
        self.assertEqual(findings, [])

    def test_share_queue_drop_is_reported_as_a_warning(self):
        self.dashboard["performance"]["shareQueueDrops"] = 2

        summary, findings = MODULE.evaluate_health(
            self.identify, self.system, self.dashboard, self.settings
        )

        self.assertTrue(summary["healthy"])
        self.assertEqual(summary["shareQueueDrops"], 2)
        self.assertTrue(any(item.level == "WARN" and "queue" in item.message for item in findings))

    def test_fan_stall_is_warning_but_shutdown_and_pool_loss_are_errors(self):
        self.dashboard["thermal"]["fans"][1]["rpm"] = 0
        self.dashboard["system"]["shutdown"] = True
        self.dashboard["stratum"]["pools"][0]["connected"] = False

        summary, findings = MODULE.evaluate_health(
            self.identify, self.system, self.dashboard, self.settings
        )

        self.assertFalse(summary["healthy"])
        self.assertTrue(any(item.level == "WARN" and "M1" in item.message for item in findings))
        self.assertGreaterEqual(sum(item.level == "ERROR" for item in findings), 2)

    def test_expected_version_mismatch_is_an_error(self):
        summary, findings = MODULE.evaluate_health(
            self.identify,
            self.system,
            self.dashboard,
            self.settings,
            expected_version="new-version",
        )

        self.assertFalse(summary["healthy"])
        self.assertTrue(any("firmware version" in item.message for item in findings))

    def test_web_ui_version_mismatch_is_an_error_and_commit_is_reported(self):
        summary, findings = MODULE.evaluate_health(
            self.identify,
            self.system,
            self.dashboard,
            self.settings,
            web_ui_build=MODULE.WebUiBuild("other-version", "abcdef1"),
        )

        self.assertFalse(summary["healthy"])
        self.assertEqual(summary["webUiVersion"], "other-version")
        self.assertEqual(summary["webUiCommit"], "abcdef1")
        self.assertTrue(any(item.level == "ERROR" and "Web UI version" in item.message
                            for item in findings))

    def test_web_ui_fetch_failure_is_an_error(self):
        summary, findings = MODULE.evaluate_health(
            self.identify,
            self.system,
            self.dashboard,
            self.settings,
            web_ui_error="main bundle unavailable",
        )

        self.assertFalse(summary["healthy"])
        self.assertTrue(any(item.level == "ERROR" and "main bundle unavailable" in item.message
                            for item in findings))

    def test_missing_build_markers_are_warning_only(self):
        summary, findings = MODULE.evaluate_health(
            self.identify,
            self.system,
            self.dashboard,
            self.settings,
            web_ui_build=MODULE.WebUiBuild(None, None),
        )

        self.assertTrue(summary["healthy"])
        self.assertTrue(any(item.level == "WARN" and "version and commit" in item.message
                            for item in findings))

    def test_fetch_web_ui_build_handles_gzip_cache_bust_and_minified_markers(self):
        index = b'<html><head><base href="/"></head><body><script src="main.abc123.js"></script></body></html>'
        bundle = (
            b'function a(){const x="test";return x.includes("VERSION")?"":x}'
            b'function b(){const y="deadbee",z=y.includes("COMMIT");return z}'
        )
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            path = urllib.parse.urlsplit(request.full_url).path
            if path == "/":
                return FakeResponse(request.full_url, gzip.compress(index), {"Content-Encoding": "gzip"})
            if path == "/main.abc123.js":
                # Some embedded servers omit Content-Encoding even though the
                # stored asset is gzip-compressed; magic-byte detection covers it.
                return FakeResponse(request.full_url, gzip.compress(bundle))
            raise AssertionError(request.full_url)

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=urlopen):
            build = MODULE.fetch_web_ui_build(
                "http://192.0.2.10/", timeout=1.5, cache_token="unit"
            )

        self.assertEqual(build, MODULE.WebUiBuild("test", "deadbee"))
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            [urllib.parse.parse_qs(urllib.parse.urlsplit(item.full_url).query)["_health"][0]
             for item in requests],
            ["unit-index", "unit-main"],
        )
        for request in requests:
            self.assertEqual(request.get_header("Accept-encoding"), "gzip")
            self.assertEqual(request.get_header("Cache-control"), "no-cache")

    def test_bundle_fetch_is_restricted_to_the_device_origin(self):
        self.assertTrue(MODULE._same_origin(
            "http://192.0.2.10/index.html", "http://192.0.2.10:80/main.js"
        ))
        self.assertFalse(MODULE._same_origin(
            "http://192.0.2.10/index.html", "http://example.com/main.js"
        ))

    def test_marker_extraction_tolerates_unminified_single_quoted_javascript(self):
        build = MODULE.extract_web_ui_build("""
            const webVersion = 'v1.2.3-test';
            return webVersion.includes('VERSION') ? '' : webVersion;
            const commitHash = '0123abc';
            const local = commitHash.includes('COMMIT');
        """)

        self.assertEqual(build, MODULE.WebUiBuild("v1.2.3-test", "0123abc"))

    def test_marker_extraction_does_not_cross_javascript_scopes(self):
        build = MODULE.extract_web_ui_build(
            'function unrelated(){const x="wrong-version";return 1}'
            'function marker(){const x="right-version";return x.includes("VERSION")?"":x}'
        )

        self.assertEqual(build.version, "right-version")

    def test_index_redirect_outside_device_origin_is_rejected(self):
        index = b'<script src="main.js"></script>'

        def urlopen(request, timeout):
            return FakeResponse("http://example.com/index.html", index)

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=urlopen):
            with self.assertRaisesRegex(RuntimeError, "index redirected outside"):
                MODULE.fetch_web_ui_build("http://192.0.2.10/", timeout=1.5, cache_token="unit")

    def test_bundle_redirect_outside_device_origin_is_rejected(self):
        index = b'<script src="main.js"></script>'
        bundle = b'const x="test";return x.includes("VERSION")?"":x'

        def urlopen(request, timeout):
            path = urllib.parse.urlsplit(request.full_url).path
            if path == "/":
                return FakeResponse(request.full_url, index)
            return FakeResponse("http://example.com/main.js", bundle)

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=urlopen):
            with self.assertRaisesRegex(RuntimeError, "bundle redirected outside"):
                MODULE.fetch_web_ui_build("http://192.0.2.10/", timeout=1.5, cache_token="unit")

    def test_invalid_gzip_is_reported_as_runtime_error(self):
        invalid_gzip = b"\x1f\x8b\x08\x00" + (b"\x00" * 6) + (b"\xff" * 30)

        with mock.patch.object(
            MODULE.urllib.request,
            "urlopen",
            return_value=FakeResponse("http://192.0.2.10/", invalid_gzip),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid gzip data"):
                MODULE._read_web_text("http://192.0.2.10/", timeout=1.5)

    def test_main_reports_ui_stream_failure_as_unhealthy_json(self):
        api_payloads = [self.identify, self.system, self.dashboard, self.settings]
        stdout = io.StringIO()
        with mock.patch.object(MODULE, "fetch_json", side_effect=api_payloads), \
             mock.patch.object(
                 MODULE,
                 "fetch_web_ui_build",
                 side_effect=http.client.IncompleteRead(b"", 1),
             ), \
             contextlib.redirect_stdout(stdout):
            exit_code = MODULE.main(["http://192.0.2.10/", "--json"])

        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(result["healthy"])
        self.assertTrue(any(item["level"] == "ERROR" and "IncompleteRead" in item["message"]
                            for item in result["findings"]))

    def test_main_preserves_json_when_api_stream_is_incomplete(self):
        stdout = io.StringIO()
        with mock.patch.object(
            MODULE,
            "fetch_json",
            side_effect=http.client.IncompleteRead(b"", 1),
        ), contextlib.redirect_stdout(stdout):
            exit_code = MODULE.main(["http://192.0.2.10/", "--json"])

        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(result["healthy"])
        self.assertIn("IncompleteRead", result["error"])

    def test_api_only_skips_web_ui_fetch_explicitly(self):
        api_payloads = [self.identify, self.system, self.dashboard, self.settings]
        stdout = io.StringIO()
        with mock.patch.object(MODULE, "fetch_json", side_effect=api_payloads), \
             mock.patch.object(MODULE, "fetch_web_ui_build") as web_fetch, \
             contextlib.redirect_stdout(stdout):
            exit_code = MODULE.main(["http://192.0.2.10/", "--json", "--api-only"])

        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["healthy"])
        self.assertIsNone(result["webUiVersion"])
        web_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
