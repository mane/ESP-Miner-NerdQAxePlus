import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "benchmark_nerdqaxe.py"
SPEC = importlib.util.spec_from_file_location("benchmark_nerdqaxe", SCRIPT_PATH)
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, url, document):
        super().__init__(json.dumps(document).encode("utf-8"))
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def geturl(self):
        return self._url


class BenchmarkNerdQAxeTest(unittest.TestCase):
    def test_request_json_is_get_only(self):
        response = FakeResponse("http://miner.local/api/v2/system", {"ok": True})
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(BENCHMARK, "READ_ONLY_OPENER", opener):
            result = BENCHMARK.request_json("http://miner.local/", "/api/v2/system", 2.0)

        self.assertEqual(result, {"ok": True})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")

    def test_redirect_handler_blocks_cross_origin(self):
        request = BENCHMARK.urllib.request.Request("http://miner.local/api/v2/system")
        handler = BENCHMARK.SameOriginRedirectHandler()
        with self.assertRaisesRegex(RuntimeError, "outside the selected device origin"):
            handler.redirect_request(
                request, None, 302, "Found", {}, "http://other.local/api/v2/system"
            )

    def test_collect_sanitizers_drop_private_configuration(self):
        metadata = BENCHMARK.sanitized_metadata(
            {"deviceModel": "NerdQAxe+", "mac": "private-mac"},
            {"asicModel": "BM1368", "ssid": "private-wifi"},
            {
                "poolURL": "private-pool",
                "poolUser": "private-user",
                "frequency": 525,
            },
        )
        sample = BENCHMARK.sanitize_dashboard(
            {
                "system": {},
                "performance": {},
                "power": {},
                "thermal": {},
                "stratum": {
                    "pools": [
                        {
                            "active": True,
                            "connected": True,
                            "url": "private-pool",
                            "user": "private-user",
                        }
                    ]
                },
            },
            0.0,
        )
        serialized = json.dumps({"metadata": metadata, "sample": sample})
        for secret in ("private-mac", "private-wifi", "private-pool", "private-user"):
            self.assertNotIn(secret, serialized)

    def test_invalid_numeric_arguments_are_rejected_before_collection(self):
        invalid_arguments = (
            ("--duration", "nan", "duration"),
            ("--duration", "inf", "duration"),
            ("--interval", "nan", "interval"),
            ("--interval", "0.5", "interval"),
            ("--timeout", "0", "timeout"),
            ("--settle-seconds", "-1", "settle-seconds"),
            ("--frequency-tolerance", "nan", "frequency-tolerance"),
        )
        for flag, value, expected in invalid_arguments:
            with self.subTest(flag=flag, value=value):
                argv = ["benchmark_nerdqaxe.py", "collect", flag, value]
                with mock.patch.object(sys, "argv", argv):
                    with self.assertRaisesRegex(SystemExit, expected):
                        BENCHMARK.main()

    def test_atomic_output_requires_force_to_replace_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "result.json"
            BENCHMARK.atomic_json_write(output, {"version": 1})
            with self.assertRaisesRegex(FileExistsError, "use --force"):
                BENCHMARK.atomic_json_write(output, {"version": 2})
            BENCHMARK.atomic_json_write(output, {"version": 2}, overwrite=True)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"version": 2})

    def test_atomic_output_never_follows_final_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "target.json"
            target.write_text('{"untouched": true}\n', encoding="utf-8")
            link = root / "result.json"
            try:
                link.symlink_to(target)
            except OSError as error:
                self.skipTest("symlinks unavailable: {}".format(error))
            with self.assertRaisesRegex(FileExistsError, "symlink"):
                BENCHMARK.atomic_json_write(link, {"untouched": False}, overwrite=True)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"untouched": True})


if __name__ == "__main__":
    unittest.main()
