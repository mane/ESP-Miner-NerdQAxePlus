#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "asic_result_task.h"
#include "serial.h"
#include "utils.h"
#include "global_state.h"
#include "nvs_config.h"
#include "system.h"
#include "boards/board.h"

#include "simple_ring64.hpp"
#include "utils.h"

static const char *TAG = "asic_result";

static SimpleRing64<32> s_seen_keys;

static portMUX_TYPE s_metricsMux = portMUX_INITIALIZER_UNLOCKED;
static uint64_t duplicateHWNonces = 0;
static uint64_t shareQueueDrops = 0;
static constexpr uint32_t SHARE_MAX_QUEUE_AGE_MS = 60000;

struct QueuedShare {
    bm_job *job;
    uint32_t nonce;
    uint32_t rolledVersion;
    TickType_t enqueuedAt;
};

struct ShareWorkerContext {
    int pool;
    QueueHandle_t queue;
    TaskHandle_t task;
    bool ready;
};

static ShareWorkerContext s_shareWorkers[2]{};

static void incrementMetric(uint64_t &metric)
{
    portENTER_CRITICAL(&s_metricsMux);
    if (metric != UINT64_MAX) {
        metric++;
    }
    portEXIT_CRITICAL(&s_metricsMux);
}

static uint64_t readMetric(const uint64_t &metric)
{
    portENTER_CRITICAL(&s_metricsMux);
    uint64_t value = metric;
    portEXIT_CRITICAL(&s_metricsMux);
    return value;
}

static void shareSubmitTask(void *pvParameters)
{
    ShareWorkerContext *worker = static_cast<ShareWorkerContext *>(pvParameters);
    QueuedShare share{};
    while (true) {
        if (xQueueReceive(worker->queue, &share, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (!share.job) {
            continue;
        }

        TickType_t age = xTaskGetTickCount() - share.enqueuedAt;
        if (age > pdMS_TO_TICKS(SHARE_MAX_QUEUE_AGE_MS)) {
            incrementMetric(shareQueueDrops);
            ESP_LOGW(TAG, "discarding stale queued share for pool %d", worker->pool);
        } else if (!POWER_MANAGEMENT_MODULE.isShutdown()) {
            STRATUM_MANAGER->submitShare(worker->pool, share.job->jobid, share.job->extranonce2,
                                         share.job->ntime, share.nonce, share.rolledVersion,
                                         share.job->version);
        }
        free_bm_job(share.job);
    }
}

static size_t startShareWorkers()
{
    size_t readyWorkers = 0;
    for (int pool = 0; pool < 2; ++pool) {
        ShareWorkerContext &worker = s_shareWorkers[pool];
        if (worker.ready && worker.queue && worker.task) {
            readyWorkers++;
            continue;
        }

        worker.pool = pool;
        worker.queue = nullptr;
        worker.task = nullptr;
        worker.ready = false;

        QueueHandle_t queue = xQueueCreate(16, sizeof(QueuedShare));
        if (!queue) {
            ESP_LOGE(TAG, "failed to allocate share queue for pool %d", pool);
            continue;
        }
        worker.queue = queue;

        const char *taskName = pool == 0 ? "share_submit_pri" : "share_submit_sec";
        if (xTaskCreatePSRAM(shareSubmitTask, taskName, 8192, &worker, 6, &worker.task) != pdPASS) {
            ESP_LOGE(TAG, "failed to start share worker for pool %d", pool);
            worker.queue = nullptr;
            worker.task = nullptr;
            vQueueDelete(queue);
            continue;
        }
        worker.ready = true;
        readyWorkers++;
    }
    return readyWorkers;
}

static bool enqueueShare(const QueuedShare &share)
{
    if (!share.job || share.job->pool_id < 0 || share.job->pool_id >= 2) {
        incrementMetric(shareQueueDrops);
        ESP_LOGE(TAG, "cannot enqueue share with invalid pool");
        return false;
    }

    ShareWorkerContext &worker = s_shareWorkers[share.job->pool_id];
    if (!worker.ready || !worker.queue || !worker.task) {
        incrementMetric(shareQueueDrops);
        ESP_LOGE(TAG, "share worker unavailable for pool %d", share.job->pool_id);
        return false;
    }

    if (xQueueSend(worker.queue, &share, 0) != pdTRUE) {
        incrementMetric(shareQueueDrops);
        ESP_LOGE(TAG, "share queue full for pool %d; dropping candidate", share.job->pool_id);
        return false;
    }
    return true;
}

static void countDuplicateHWNonces() {
    incrementMetric(duplicateHWNonces);
}

uint64_t getDuplicateHWNonces() {
    return readMetric(duplicateHWNonces);
}

uint64_t getShareQueueDrops() {
    return readMetric(shareQueueDrops);
}

// Hash every field that determines the PoW header. Pool job IDs may be reused
// after reconnects, while equal header+nonce+version values are true duplicates.
static uint64_t make_key(const bm_job *job, uint32_t nonce, uint32_t version)
{
    uint64_t hash = 1469598103934665603ULL;
    const auto mix = [&hash](const void *data, size_t length) {
        const uint8_t *bytes = static_cast<const uint8_t *>(data);
        for (size_t i = 0; i < length; ++i) {
            hash ^= bytes[i];
            hash *= 1099511628211ULL;
        }
    };
    mix(&version, sizeof(version));
    mix(job->prev_block_hash, sizeof(job->prev_block_hash));
    mix(job->merkle_root, sizeof(job->merkle_root));
    mix(&job->ntime, sizeof(job->ntime));
    mix(&job->target, sizeof(job->target));
    mix(&nonce, sizeof(nonce));
    mix(&job->pool_id, sizeof(job->pool_id));
    return hash;
}

void ASIC_result_task(void *pvParameters)
{
    Board* board = SYSTEM_MODULE.getBoard();
    Asic* asics = board->getAsics();
    size_t readyShareWorkers = startShareWorkers();
    if (readyShareWorkers != 2) {
        ESP_LOGE(TAG, "share submission degraded: %u/2 workers ready", (unsigned int) readyShareWorkers);
    }

    while (1) {
        if (POWER_MANAGEMENT_MODULE.isShutdown()) {
            ESP_LOGW(TAG, "suspended");
            vTaskSuspend(NULL);
        }
        //ESP_LOGI("Memory", "%lu", esp_get_free_heap_size()); test
        task_result asic_result;

        // get the result
        if (!asics->processWork(&asic_result)) {
            continue;
        }

        if (asic_result.is_reg_resp) {
            switch (asic_result.reg) {
                case 0xb4: {
                    if (asic_result.data & 0x80000000) {
                        float ftemp = (float) (asic_result.data & 0x0000ffff) * 0.171342f - 299.5144f;
                        ESP_LOGI(TAG, "asic %d temp: %.3f", (int) asic_result.asic_nr, ftemp);
                        board->setChipTemp(asic_result.asic_nr, ftemp);
                    }
                    break;
                }
                case 0x48: {
                    // maybe the upper 32bit of a 64bit counter and 0x90 returns the lower 32bit
                    break;
                }
                case 0x90: {
                    HASHRATE_MONITOR.onRegisterReply(asic_result.asic_nr, asic_result.data);
                    break;
                }
                default: {
                    // NOP
                    break;
                }
            }
            continue;
        }

        uint8_t asic_job_id = asic_result.job_id;

        bm_job *job = asicJobs.getClone(asic_job_id);
        if (!job) {
            //ESP_LOGI(TAG, "Invalid job id found, 0x%02X", asic_job_id);
            continue;
        }
        if (job->pool_id < 0 || job->pool_id >= 2) {
            ESP_LOGE(TAG, "Invalid pool id %d in ASIC job", job->pool_id);
            free_bm_job(job);
            continue;
        }

        // now we have the original job and can `or` the version
        asic_result.rolled_version |= job->version;

        // check the nonce difficulty
        double nonce_diff = test_nonce_value(job, asic_result.nonce, asic_result.rolled_version);

        // get best known session diff
        char bestDiffString[16];
        suffixString(STRATUM_MANAGER->getBestSessionDiff(), bestDiffString, sizeof(bestDiffString), 3);

        const char *pool_str = job->pool_id ? "Sec" : "Pri";

        // log the ASIC response, including pool and best session difficulty using human-readable SI formatting
        // we only show responses >= maxAsicDifficulty to avoid spamming the log
        // change for dual pool because the pool with lower % can reduce asic HW difficulty
        if (nonce_diff >= board->getAsicMaxDifficulty() || nonce_diff >= job->pool_diff) {
            ESP_LOGI(TAG, "(%s) Job ID: %02X AsicNr: %d Ver: %08" PRIX32 " Nonce %08" PRIX32 "; Extranonce2 %s diff %.1f/%lu/%s",
                pool_str, asic_job_id, asic_result.asic_nr, asic_result.rolled_version, asic_result.nonce, job->extranonce2,
                nonce_diff, job->pool_diff, bestDiffString);
        }

        uint64_t key = make_key(job, asic_result.nonce, asic_result.rolled_version);
        bool duplicate = !s_seen_keys.insert_if_absent(key);
        if (duplicate) {
            ESP_LOGW(TAG, "(%s) duplicate share detected!", pool_str);
            countDuplicateHWNonces();
        }

        if (!duplicate) {
            if (nonce_diff >= board->getAsicMaxDifficulty()) {
                SYSTEM_MODULE.pushShare(asic_result.asic_nr);
            }
            STRATUM_MANAGER->checkForBestDiff(job->pool_id, nonce_diff, job->target);
            STRATUM_MANAGER->checkForFoundBlock(job->pool_id, nonce_diff, job->target);
        }

        // Never perform socket/TLS I/O in the high-priority UART drain task.
        // Transfer ownership of the immutable job clone to a per-pool worker;
        // known duplicates are dropped locally instead of becoming rejects.
        if (!duplicate && nonce_diff >= job->pool_diff) {
            QueuedShare share{job, asic_result.nonce, asic_result.rolled_version, xTaskGetTickCount()};
            if (enqueueShare(share)) {
                job = nullptr;
            }
        }

        if (job) {
            free_bm_job(job);
        }
    }
}
