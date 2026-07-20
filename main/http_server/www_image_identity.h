#pragma once

#include <cstddef>
#include <cstdint>

enum class WwwImageIdentityStatus : uint8_t {
    OK = 0,
    INVALID_ARGUMENT,
    MISSING,
    DUPLICATE,
    MALFORMED,
    VERSION_MISMATCH,
};

// Validate the release identity embedded as a small plain-text file in the raw
// SPIFFS image. This parser is intentionally firmware-independent so its exact
// fail-closed behaviour can also be exercised by host-side tests.
WwwImageIdentityStatus validate_nerdqaxeplus_www_image(const uint8_t *image, size_t image_size,
                                                       const char *expected_version);
const char *www_image_identity_status_message(WwwImageIdentityStatus status);
