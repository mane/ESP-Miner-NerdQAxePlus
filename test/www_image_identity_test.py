from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include "www_image_identity.h"

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <string>
#include <vector>

static constexpr const char *CANDIDATE_VERSION = "v1.1.1-mane.6-nqa-lts8";
static constexpr const char *RUNNING_VERSION = "v1.1.1-mane.6-nqa-lts7";
static constexpr const char *MAGIC = "NERDQAXEPLUS_WEB_IDENTITY_V1";

static std::vector<uint8_t> imageWithManifest(const std::string &version, const std::string &commit)
{
    std::vector<uint8_t> image(4096, 0xff);
    const std::string manifest = std::string(MAGIC) + "\nVERSION_TAG=" + version +
                                 "\nCOMMIT_HASH=" + commit + "\n";
    image.insert(image.begin() + 512, manifest.begin(), manifest.end());
    image.resize(4096);
    return image;
}

static void expectStatus(const std::vector<uint8_t> &image, const char *expectedVersion,
                         WwwImageIdentityStatus expected)
{
    assert(validate_nerdqaxeplus_www_image(image.data(), image.size(), expectedVersion) == expected);
}

int main()
{
    // Factory OTA must accept a WWW image paired with the candidate firmware,
    // even when the currently running firmware is one release older.
    expectStatus(imageWithManifest(CANDIDATE_VERSION, "ad96b01"), CANDIDATE_VERSION,
                 WwwImageIdentityStatus::OK);
    expectStatus(imageWithManifest(CANDIDATE_VERSION, "ad96b01"), RUNNING_VERSION,
                 WwwImageIdentityStatus::VERSION_MISMATCH);
    expectStatus(std::vector<uint8_t>(4096, 0xff), CANDIDATE_VERSION, WwwImageIdentityStatus::MISSING);
    expectStatus(imageWithManifest(CANDIDATE_VERSION, ""), CANDIDATE_VERSION, WwwImageIdentityStatus::MALFORMED);
    expectStatus(imageWithManifest(CANDIDATE_VERSION, "bad/commit"), CANDIDATE_VERSION,
                 WwwImageIdentityStatus::MALFORMED);
    expectStatus(imageWithManifest(CANDIDATE_VERSION, std::string(65, 'a')), CANDIDATE_VERSION,
                 WwwImageIdentityStatus::MALFORMED);

    auto missingVersionPrefix = imageWithManifest(CANDIDATE_VERSION, "ad96b01");
    const std::string versionPrefix = "VERSION_TAG=";
    for (size_t offset = 0; offset + versionPrefix.size() <= missingVersionPrefix.size(); ++offset) {
        if (std::equal(versionPrefix.begin(), versionPrefix.end(), missingVersionPrefix.begin() + offset)) {
            missingVersionPrefix[offset] = 'X';
            break;
        }
    }
    expectStatus(missingVersionPrefix, CANDIDATE_VERSION, WwwImageIdentityStatus::MALFORMED);

    auto duplicate = imageWithManifest(CANDIDATE_VERSION, "ad96b01");
    const std::string second = std::string(MAGIC) + "\nVERSION_TAG=" + CANDIDATE_VERSION +
                               "\nCOMMIT_HASH=ad96b01\n";
    std::copy(second.begin(), second.end(), duplicate.begin() + 2048);
    expectStatus(duplicate, CANDIDATE_VERSION, WwwImageIdentityStatus::DUPLICATE);

    assert(validate_nerdqaxeplus_www_image(nullptr, 0, CANDIDATE_VERSION) ==
           WwwImageIdentityStatus::INVALID_ARGUMENT);
    assert(validate_nerdqaxeplus_www_image(duplicate.data(), duplicate.size(), "") ==
           WwwImageIdentityStatus::INVALID_ARGUMENT);
    return 0;
}
"""


class WwwImageIdentityTest(unittest.TestCase):
    def test_parser_fails_closed_before_firmware_integration(self) -> None:
        compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "A C++ compiler is required for this test")

        with tempfile.TemporaryDirectory(prefix="www-image-identity-") as temp_dir:
            temp = Path(temp_dir)
            harness = temp / "www_image_identity_harness.cpp"
            binary = temp / "www_image_identity_harness"
            harness.write_text(textwrap.dedent(HARNESS))

            build = subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(ROOT / "main/http_server"),
                    str(harness),
                    str(ROOT / "main/http_server/www_image_identity.cpp"),
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

    def test_manual_and_recovery_route_validate_before_flash_mutation(self) -> None:
        source = (ROOT / "main/http_server/handler_ota.cpp").read_text()
        handler = source[
            source.index("esp_err_t POST_WWW_update") : source.index("esp_err_t POST_OTA_update")
        ]

        validation = handler.index("validate_nerdqaxeplus_www_image")
        self.assertLess(handler.index("received_total < WWW_IMAGE_SIZE"), validation)
        self.assertLess(validation, handler.index("esp_vfs_spiffs_unregister"))
        self.assertLess(validation, handler.index("esp_partition_erase_range"))


if __name__ == "__main__":
    unittest.main()
