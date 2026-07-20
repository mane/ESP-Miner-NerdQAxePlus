#include "global_state.h"
#include "hashrate_monitor_task.h"
#include "boards/board.h"
#include "esp_log.h"
#include "mining.h"
#include "utils.h"
#include <math.h>
#include <new>

static const char *HR_TAG = "hashrate_monitor";
static constexpr uint8_t REG_NONCE_TOTAL_CNT = 0x90;

HashrateMonitor::HashrateMonitor()
{}

bool HashrateMonitor::start(Board *board, Asic *asic)
{
    if (!board || !asic) {
        ESP_LOGE(HR_TAG, "start(): missing dependencies (board=%p, asic=%p)", (void *) board, (void *) asic);
        return false;
    }

    const int asicCount = board->getAsicCount();
    if (asicCount <= 0) {
        ESP_LOGE(HR_TAG, "start(): invalid ASIC count");
        return false;
    }

    // Allocate privately first. Readers must never observe a positive count with
    // partially initialized arrays, nor arrays that a failure path may free.
    float *chipHashrate = new (std::nothrow) float[asicCount]();
    uint32_t *chipHashrateUpdatedMs = new (std::nothrow) uint32_t[asicCount]();
    int64_t *prevResponse = new (std::nothrow) int64_t[asicCount]();
    uint32_t *prevCounter = new (std::nothrow) uint32_t[asicCount]();
    if (!chipHashrate || !chipHashrateUpdatedMs || !prevResponse || !prevCounter) {
        ESP_LOGE(HR_TAG, "start(): allocation failed");
        delete[] chipHashrate;
        delete[] chipHashrateUpdatedMs;
        delete[] prevResponse;
        delete[] prevCounter;
        return false;
    }

    pthread_mutex_lock(&m_mutex);
    if (m_board || m_asic || m_asicCount != 0 || m_chipHashrate ||
        m_chipHashrateUpdatedMs || m_prevResponse || m_prevCounter) {
        pthread_mutex_unlock(&m_mutex);
        delete[] chipHashrate;
        delete[] chipHashrateUpdatedMs;
        delete[] prevResponse;
        delete[] prevCounter;
        ESP_LOGE(HR_TAG, "start(): monitor already started");
        return false;
    }

    m_board = board;
    m_asic = asic;
    m_period_ms = HR_INTERVAL;
    m_chipHashrate = chipHashrate;
    m_chipHashrateUpdatedMs = chipHashrateUpdatedMs;
    m_prevResponse = prevResponse;
    m_prevCounter = prevCounter;
    m_asicCount = asicCount;
    pthread_mutex_unlock(&m_mutex);

    if (xTaskCreatePSRAM(&HashrateMonitor::taskWrapper, "hr_monitor", 4096, (void *) this, 10, NULL) != pdPASS) {
        ESP_LOGE(HR_TAG, "start(): task creation failed");

        // Detach the complete state under the same mutex used by snapshots and
        // RX updates. Deallocation happens only after every reader has left.
        pthread_mutex_lock(&m_mutex);
        chipHashrate = m_chipHashrate;
        chipHashrateUpdatedMs = m_chipHashrateUpdatedMs;
        prevResponse = m_prevResponse;
        prevCounter = m_prevCounter;
        m_asicCount = 0;
        m_chipHashrate = nullptr;
        m_chipHashrateUpdatedMs = nullptr;
        m_prevResponse = nullptr;
        m_prevCounter = nullptr;
        m_board = nullptr;
        m_asic = nullptr;
        pthread_mutex_unlock(&m_mutex);

        delete[] chipHashrate;
        delete[] chipHashrateUpdatedMs;
        delete[] prevResponse;
        delete[] prevCounter;
        return false;
    }
    ESP_LOGI(HR_TAG, "started (period=%lums)", m_period_ms);
    return true;
}

void HashrateMonitor::taskWrapper(void *pv)
{
    auto *self = static_cast<HashrateMonitor *>(pv);
    self->taskLoop();
}

bool HashrateMonitor::publishTotalIfComplete()
{
    size_t offset = 0;
    float total = 0.0f;
    bool complete = true;
    // Iterate through each ASIC and append its count to the log message
    pthread_mutex_lock(&m_mutex);
    const uint32_t nowMs = (uint32_t) (esp_timer_get_time() / 1000ULL);
    for (int i = 0; i < m_asicCount; i++) {
        const float chipHashrate = m_chipHashrate[i];
        const uint32_t updatedMs = m_chipHashrateUpdatedMs[i];
        total += chipHashrate;
        if (!isfinite(chipHashrate) || chipHashrate <= 0.0f || updatedMs == 0 ||
            nowMs - updatedMs > 2U * HR_INTERVAL) {
            complete = false;
        }
        size_t remaining = sizeof(m_logBuffer) - offset;
        int written = snprintf(m_logBuffer + offset, remaining, "%.2fGH/s / ", chipHashrate);
        if (written < 0) {
            pthread_mutex_unlock(&m_mutex);
            return false;
        }
        if (static_cast<size_t>(written) >= remaining) {
            offset = sizeof(m_logBuffer) - 1;
            break;
        }
        offset += static_cast<size_t>(written);
    }
    pthread_mutex_unlock(&m_mutex);
    if (offset >= 2) {
        m_logBuffer[offset - 2] = 0; // remove trailing slash
    }

    // Apply a small median filter to remove transient counter outliers.
    const float filteredHashrate = m_median.update(total);
    m_hashrate.store(filteredHashrate, std::memory_order_relaxed);

    ESP_LOGI(HR_TAG, "chip hashrates: %s (total: %.3fGH/s%s)", m_logBuffer,
             filteredHashrate, complete ? "" : ", incomplete");
    return complete;
}

void HashrateMonitor::taskLoop()
{
    // Small startup delay
    vTaskDelay(pdMS_TO_TICKS(4000));

    // Send broadcast RESET for counter register once
    m_asic->resetCounter(REG_NONCE_TOTAL_CNT);

    TickType_t lastWake = xTaskGetTickCount();
    while (1) {
        if (POWER_MANAGEMENT_MODULE.isShutdown()) {
            ESP_LOGW(HR_TAG, "suspended");
            vTaskSuspend(NULL);
        }

        if (!m_board || !m_asic) {
            vTaskDelay(pdMS_TO_TICKS(m_period_ms));
            continue;
        }

        // read the counters
        m_asic->readCounter(REG_NONCE_TOTAL_CNT);

        // responses normally take 20-30ms, so this is safe
        vTaskDelay(pdMS_TO_TICKS(500));

        const bool complete = publishTotalIfComplete();

        // apply a slight smoothing
        const float currentHashrate = m_hashrate.load(std::memory_order_relaxed);
        float smoothedHashrate = m_smoothedHashrate.load(std::memory_order_relaxed);
        if (!smoothedHashrate) {
            smoothedHashrate = currentHashrate;
        }

        smoothedHashrate = 0.5f * smoothedHashrate + 0.5f * currentHashrate;
        m_smoothedHashrate.store(smoothedHashrate, std::memory_order_relaxed);
        if (complete && isfinite(smoothedHashrate) && smoothedHashrate > 0.0f) {
            m_lastCompleteHashrateMs.store(
                (uint32_t) (esp_timer_get_time() / 1000ULL), std::memory_order_release);
        }

        vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(m_period_ms));
    }
}

bool HashrateMonitor::getFreshSmoothedTotalChipHashrate(uint64_t nowMs, uint32_t maxAgeMs,
                                                        float *hashrateGhs) const
{
    if (!hashrateGhs || maxAgeMs == 0) {
        return false;
    }
    const uint32_t lastCompleteMs = m_lastCompleteHashrateMs.load(std::memory_order_acquire);
    const uint32_t now32 = (uint32_t) nowMs;
    if (lastCompleteMs == 0 || now32 - lastCompleteMs > maxAgeMs) {
        return false;
    }
    const float value = m_smoothedHashrate.load(std::memory_order_relaxed);
    if (!isfinite(value) || value <= 0.0f) {
        return false;
    }
    *hashrateGhs = value;
    return true;
}

size_t HashrateMonitor::copyChipHashrateSnapshot(ChipHashrateSample *samples, size_t capacity) const
{
    if (!samples || capacity == 0) {
        return 0;
    }

    pthread_mutex_lock(&m_mutex);
    if (!m_chipHashrate || !m_chipHashrateUpdatedMs || m_asicCount <= 0) {
        pthread_mutex_unlock(&m_mutex);
        return 0;
    }

    // Capture time while holding the update lock. Every timestamp in this
    // snapshot is therefore from the past; uint32_t subtraction remains valid
    // across the monotonic millisecond counter wrap.
    const uint32_t nowMs = (uint32_t) (esp_timer_get_time() / 1000ULL);
    const size_t asicCount = (size_t) m_asicCount;
    const size_t count = capacity < asicCount ? capacity : asicCount;
    for (size_t i = 0; i < count; ++i) {
        const float hashrate = m_chipHashrate[i];
        const uint32_t updatedMs = m_chipHashrateUpdatedMs[i];
        samples[i].hashrateGhs = isfinite(hashrate) && hashrate >= 0.0f ? hashrate : 0.0f;
        samples[i].ageMs = updatedMs == 0 ? UINT32_MAX : nowMs - updatedMs;
    }
    pthread_mutex_unlock(&m_mutex);
    return count;
}

void HashrateMonitor::onRegisterReply(uint8_t asic_idx, uint32_t counterNow)
{
    pthread_mutex_lock(&m_mutex);
    if (asic_idx >= m_asicCount || !m_chipHashrate || !m_chipHashrateUpdatedMs ||
        !m_prevResponse || !m_prevCounter) {
        pthread_mutex_unlock(&m_mutex);
        ESP_LOGE(HR_TAG, "response for invalid ASIC %d", (int) asic_idx);
        return;
    }

    const int64_t now = esp_timer_get_time();

    // first response
    if (!m_prevResponse[asic_idx]) {
        m_prevResponse[asic_idx] = now;
        m_prevCounter[asic_idx] = counterNow;
        pthread_mutex_unlock(&m_mutex);
        return;
    }

    int64_t timeDelta = now - m_prevResponse[asic_idx];
    uint32_t counterDelta = counterNow - m_prevCounter[asic_idx];

    if (timeDelta <= 0) {
        m_prevCounter[asic_idx] = counterNow;
        m_prevResponse[asic_idx] = now;
        pthread_mutex_unlock(&m_mutex);
        ESP_LOGW(HR_TAG, "ignoring non-monotonic counter timestamp for ASIC %d", (int)asic_idx);
        return;
    }

    double chip_ghs = (double) counterDelta * (double) 0x100000000uLL / (double) timeDelta / 1000.0;
//    ESP_LOGE("XXX", "m_prevResponse[%d]=%lld now=%lld m_prevCounter[%d]=%lu counterNow=%lu timeDelta=%llu counterDelta=%lu chip_ghs=%.3f",
//        (int) asic_idx, m_prevResponse[asic_idx], now, (int) asic_idx, m_prevCounter[asic_idx], counterNow, timeDelta, counterDelta, chip_ghs);

    m_chipHashrate[asic_idx] = (float) chip_ghs;
    m_chipHashrateUpdatedMs[asic_idx] = (uint32_t) (now / 1000ULL);
    m_prevCounter[asic_idx] = counterNow;
    m_prevResponse[asic_idx] = now;
    pthread_mutex_unlock(&m_mutex);
}
