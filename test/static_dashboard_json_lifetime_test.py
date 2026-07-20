from pathlib import Path
import re
import unittest


REPO = Path(__file__).resolve().parents[1]


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace_start = source.index("{", start)
    depth = 0
    for index in range(brace_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start : index + 1]
    raise AssertionError(f"Could not find body for {signature}")


class JsonLifetimeContractTest(unittest.TestCase):
    def assert_owned_copy_helper(self, source: str) -> None:
        body = _function_body(source, "static JsonString copiedJsonStringOrEmpty")
        self.assertRegex(body, re.compile(r'if\s*\(\s*!value\s*\)\s*\{\s*value\s*=\s*""\s*;', re.S))
        self.assertRegex(body, re.compile(r'return\s+JsonString\(value,\s*false\)\s*;'))

    def assert_copied_assignment(
        self, body: str, destination: str, source: str, count: int = 1
    ) -> None:
        pattern = re.compile(
            rf'{re.escape(destination)}\s*=\s*copiedJsonStringOrEmpty\('
            rf'{re.escape(source)}\)\s*;'
        )
        self.assertEqual(len(pattern.findall(body)), count)

    def test_stack_local_scriptsig_is_copied_into_json_document(self) -> None:
        source = (REPO / "main/http_server/v2/handler_v2_dashboard.cpp").read_text()
        body = _function_body(source, "esp_err_t GET_V2_dashboard")

        self.assertRegex(
            body,
            re.compile(
                r'bh\["scriptSig"\]\s*=\s*JsonString\(cb\.scriptsig,\s*false\)\s*;'
            ),
        )
        self.assertNotRegex(
            body,
            re.compile(r'bh\["scriptSig"\]\s*=\s*cb\.scriptsig\s*;'),
        )

    def test_legacy_sv2_keys_are_owned_before_nvs_buffers_are_freed(self) -> None:
        source = (REPO / "main/http_server/handler_system.cpp").read_text()
        body = _function_body(source, "esp_err_t GET_system_info")

        self.assert_owned_copy_helper(source)
        self.assert_copied_assignment(
            body, 'doc["sv2AuthorityPubkey"]', "sv2_auth"
        )
        self.assert_copied_assignment(
            body, 'doc["fallbackSv2AuthorityPubkey"]', "fb_sv2_auth"
        )
        self.assertLess(
            body.index("copiedJsonStringOrEmpty(sv2_auth)"),
            body.index("safe_free(sv2_auth)"),
        )
        self.assertLess(
            body.index("copiedJsonStringOrEmpty(fb_sv2_auth)"),
            body.index("safe_free(fb_sv2_auth)"),
        )

    def test_v2_settings_heap_strings_are_owned_before_release(self) -> None:
        source = (REPO / "main/http_server/v2/handler_v2_settings.cpp").read_text()
        body = _function_body(source, "esp_err_t GET_V2_settings")

        self.assert_owned_copy_helper(source)
        self.assert_copied_assignment(body, 'pool["url"]', "url", count=2)
        self.assert_copied_assignment(body, 'pool["user"]', "user", count=2)
        self.assert_copied_assignment(
            body, 'pool["sv2AuthorityPubkey"]', "sv2", count=2
        )
        self.assert_copied_assignment(body, 'doc["hostname"]', "hostname")
        self.assert_copied_assignment(body, 'doc["ssid"]', "ssid")
        self.assert_copied_assignment(body, 'doc["mempoolUrl"]', "mempoolUrl")

        self.assertNotRegex(
            body,
            re.compile(
                r'(?:pool|doc)\[[^\]]+\]\s*=\s*'
                r'(?:url|user|sv2|hostname|ssid|mempoolUrl)\s*\?'
            ),
        )

    def test_v2_dashboard_pool_identity_strings_are_owned_before_release(self) -> None:
        source = (REPO / "main/http_server/v2/handler_v2_dashboard.cpp").read_text()
        body = _function_body(source, "esp_err_t GET_V2_dashboard")

        self.assert_owned_copy_helper(source)
        self.assert_copied_assignment(body, 'pool["host"]', "urls[i]")
        self.assert_copied_assignment(body, 'pool["user"]', "users[i]")
        self.assertLess(
            body.index("copiedJsonStringOrEmpty(urls[i])"),
            body.index("free(urls[i])"),
        )
        self.assertLess(
            body.index("copiedJsonStringOrEmpty(users[i])"),
            body.index("free(users[i])"),
        )


if __name__ == "__main__":
    unittest.main()
