#pragma once

#include <cstdint>

// A single device-wide reservation protects every path capable of writing an
// application or WWW partition. The factory updater reserves in the HTTP task
// and adopts in its worker; synchronous handlers acquire directly via RAII.
bool ota_operation_try_acquire();
void ota_operation_release();

enum class OtaOperationLockMode : uint8_t {
    TRY_ACQUIRE,
    ADOPT_ACQUIRED,
};

class OtaOperationGuard {
public:
    explicit OtaOperationGuard(OtaOperationLockMode mode = OtaOperationLockMode::TRY_ACQUIRE);
    ~OtaOperationGuard();

    OtaOperationGuard(const OtaOperationGuard &) = delete;
    OtaOperationGuard &operator=(const OtaOperationGuard &) = delete;

    bool acquired() const { return m_acquired; }

private:
    bool m_acquired;
};
