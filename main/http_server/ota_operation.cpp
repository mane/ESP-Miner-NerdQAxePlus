#include "ota_operation.h"

#include <atomic>

namespace {
std::atomic<bool> ota_operation_active{false};
}

bool ota_operation_try_acquire()
{
    bool expected = false;
    return ota_operation_active.compare_exchange_strong(expected, true, std::memory_order_acq_rel,
                                                        std::memory_order_acquire);
}

void ota_operation_release()
{
    ota_operation_active.store(false, std::memory_order_release);
}

OtaOperationGuard::OtaOperationGuard(OtaOperationLockMode mode)
    : m_acquired(mode == OtaOperationLockMode::ADOPT_ACQUIRED || ota_operation_try_acquire())
{
}

OtaOperationGuard::~OtaOperationGuard()
{
    if (m_acquired) {
        ota_operation_release();
    }
}
