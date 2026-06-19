import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GITHUB_UPDATE_SERVICE = ROOT / "main/http_server/axe-os/src/app/services/github-update.service.ts"
OTA_FACTORY_HANDLER = ROOT / "main/http_server/handler_ota_factory.cpp"
OTP_INTERCEPTOR_SPEC = ROOT / "main/http_server/axe-os/src/app/services/otp-session.interceptor.spec.ts"

FORK_API_RELEASES = "https://api.github.com/repos/mane/ESP-Miner-NerdQAxePlus/releases"
FORK_DOWNLOAD_PREFIX = "https://github.com/mane/ESP-Miner-NerdQAxePlus/releases/download/"
UPSTREAM_API_OWNER = "api.github.com/repos/shufps/ESP-Miner-NerdQAxePlus"
UPSTREAM_DOWNLOAD_OWNER = "https://github.com/shufps/"


class ForkUpdaterEndpointTest(unittest.TestCase):
    def test_web_updater_fetches_releases_from_fork(self):
        source = GITHUB_UPDATE_SERVICE.read_text()
        self.assertIn(FORK_API_RELEASES, source)
        self.assertNotIn(UPSTREAM_API_OWNER, source)

    def test_backend_allows_only_fork_release_asset_urls(self):
        source = OTA_FACTORY_HANDLER.read_text()
        self.assertIn(FORK_DOWNLOAD_PREFIX, source)
        self.assertIn("GITHUB_RELEASE_DOWNLOAD_PREFIX", source)
        self.assertRegex(
            source,
            re.compile(
                r"strncasecmp\(url,\s*GITHUB_RELEASE_DOWNLOAD_PREFIX,\s*strlen\(GITHUB_RELEASE_DOWNLOAD_PREFIX\)\)"
            ),
        )
        self.assertNotIn("#define GITHUB_REPO", source)
        self.assertNotIn(UPSTREAM_DOWNLOAD_OWNER, source)

    def test_external_otp_interceptor_case_uses_fork_github_origin(self):
        source = OTP_INTERCEPTOR_SPEC.read_text()
        self.assertIn(f"{FORK_API_RELEASES}/latest", source)
        self.assertNotIn(UPSTREAM_API_OWNER, source)


if __name__ == "__main__":
    unittest.main()
