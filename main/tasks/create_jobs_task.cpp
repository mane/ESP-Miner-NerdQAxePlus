#include <limits.h>
#include <pthread.h>
#include <string.h>
#include <sys/time.h>

#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "asic.h"
#include "mining.h"

#include "global_state.h"
#include "create_jobs_task.h"
#include "can_sender.h"
#include "can_master_task.h"

#include "boards/board.h"
#include "macros.h"
#include "system.h"

#define PRIMARY 0
#define SECONDARY 1

static const char *TAG = "create_jobs_task";

pthread_mutex_t job_mutex = PTHREAD_MUTEX_INITIALIZER;
pthread_cond_t job_cond = PTHREAD_COND_INITIALIZER;
static bool job_wake_pending = false;

pthread_mutex_t current_stratum_job_mutex = PTHREAD_MUTEX_INITIALIZER;
uint64_t miningJobGeneration[2] = {0, 0};

void clean_asic_jobs_for_pool_locked(int pool)
{
    asicJobs.cleanJobs(pool);
    for (int slave = 0; slave < CAN_SLAVE_MAX; ++slave) {
        slaveAsicJobs[slave].cleanJobs(pool);
    }
}

// ============================================================================
// MiningInfoBase - abstract interface for protocol-agnostic job construction
// ============================================================================

MiningInfoBase::~MiningInfoBase() {}

// ============================================================================
// MiningInfoV1 - Stratum V1 job construction
// ============================================================================

class MiningInfoV1 : public MiningInfoBase {
  public:
    mining_notify *current_job = nullptr;

    char *extranonce_str = nullptr;
    int extranonce_2_len = 0;

    char *next_extranonce_str = nullptr;
    int next_extranonce_2_len = 0;

    uint32_t stratum_difficulty = 8192;
    uint32_t active_stratum_difficulty = 8192;
    uint32_t version_mask = ASIC_DEFAULT_VERSION_MASK;

  public:
    MiningInfoV1()
    {
        current_job = (mining_notify *) CALLOC(1, sizeof(mining_notify));
        if (!current_job) {
            ESP_LOGE(TAG, "Failed to allocate V1 mining state");
        }
    }

    ~MiningInfoV1() override
    {
        safe_free(extranonce_str);
        safe_free(next_extranonce_str);
        if (current_job) {
            safe_free(current_job->job_id);
            safe_free(current_job->coinbase_1);
            safe_free(current_job->coinbase_2);
            free(current_job);
        }
    }

    // --- MiningInfoBase interface ---

    bm_job* buildBmJob(uint32_t extranonce_2, int pool_id, uint32_t asic_diff) override
    {
        if (!isValid() || extranonce_2_len <= 0 || extranonce_2_len > MAX_EXTRANONCE_SIZE) {
            return nullptr;
        }

        // generate extranonce2 hex string
        char extranonce_2_str[MAX_EXTRANONCE_SIZE * 2 + 1];
        int en2_chars = extranonce_2_len * 2;
        uint32_t encoded_extranonce = extranonce_2;
        if (extranonce_2_len < static_cast<int>(sizeof(encoded_extranonce))) {
            encoded_extranonce &= (1U << (extranonce_2_len * 8)) - 1U;
        }
        int written = snprintf(extranonce_2_str, sizeof(extranonce_2_str), "%0*lx",
                               en2_chars, (unsigned long)encoded_extranonce);
        if (written != en2_chars) {
            ESP_LOGE(TAG, "Failed to format extranonce2 (size=%d)", extranonce_2_len);
            return nullptr;
        }

        // generate coinbase tx
        size_t coinbase_tx_len = strlen(current_job->coinbase_1) + strlen(extranonce_str) + strlen(extranonce_2_str) +
                                 strlen(current_job->coinbase_2);
        if (coinbase_tx_len == SIZE_MAX) {
            return nullptr;
        }
        char *coinbase_tx = (char *) MALLOC(coinbase_tx_len + 1);
        if (!coinbase_tx) {
            ESP_LOGE(TAG, "Failed to allocate coinbase transaction");
            return nullptr;
        }
        written = snprintf(coinbase_tx, coinbase_tx_len + 1, "%s%s%s%s", current_job->coinbase_1, extranonce_str,
                           extranonce_2_str, current_job->coinbase_2);
        if (written < 0 || static_cast<size_t>(written) != coinbase_tx_len) {
            free(coinbase_tx);
            return nullptr;
        }

        // calculate merkle root
        char merkle_root[65];
        bool merkle_ok = calculate_merkle_root_hash(coinbase_tx, current_job->_merkle_branches,
                                                    current_job->n_merkle_branches, merkle_root);
        free(coinbase_tx);
        if (!merkle_ok) {
            ESP_LOGE(TAG, "Failed to calculate Merkle root");
            return nullptr;
        }

        // we need malloc because we will save it in the job array
        bm_job *next_job = (bm_job *) CALLOC(1, sizeof(bm_job));
        if (!next_job) {
            ESP_LOGE(TAG, "Failed to allocate ASIC job");
            return nullptr;
        }
        construct_bm_job(current_job, merkle_root, version_mask, next_job);
        next_job->jobid = strdup(current_job->job_id);
        next_job->extranonce2 = strdup(extranonce_2_str);
        if (!next_job->jobid || !next_job->extranonce2) {
            free_bm_job(next_job);
            return nullptr;
        }
        next_job->pool_diff = active_stratum_difficulty;
        next_job->pool_id = pool_id;
        next_job->asic_diff = asic_diff;

        return next_job;
    }

    bool isValid() const override
    {
        return current_job && current_job->ntime != 0 && current_job->job_id && current_job->coinbase_1 &&
               current_job->coinbase_2 && extranonce_str && extranonce_2_len > 0 &&
               extranonce_2_len <= MAX_EXTRANONCE_SIZE;
    }

    bool isNewWork(uint32_t &last_ntime) const override
    {
        if (!current_job) {
            return false;
        }
        if (last_ntime != current_job->ntime) {
            last_ntime = current_job->ntime;
            return true;
        }
        return false;
    }

    const char* getJobId() const override
    {
        return current_job ? current_job->job_id : nullptr;
    }

    uint32_t getActiveDifficulty() const override
    {
        return active_stratum_difficulty;
    }

    uint32_t getVersionMask() const override
    {
        return version_mask;
    }

    void invalidate() override
    {
        if (!current_job) {
            return;
        }
        // mark as invalid
        current_job->ntime = 0;
        safe_free(extranonce_str);
        safe_free(next_extranonce_str);
        safe_free(current_job->job_id);
        safe_free(current_job->coinbase_1);
        safe_free(current_job->coinbase_2);
    }

    // --- V1-specific methods ---

    void set_version_mask(uint32_t mask)
    {
        version_mask = mask;
    }

    bool set_difficulty(uint32_t difficulty)
    {
        // new difficulty?
        bool is_new = stratum_difficulty != difficulty;

        // set difficulty
        stratum_difficulty = difficulty;
        return is_new;
    }

    bool set_enonce(const char *enonce, int enonce2_len)
    {
        if (!enonce || enonce2_len <= 0 || enonce2_len > MAX_EXTRANONCE_SIZE) {
            return false;
        }
        char *copy = strdup(enonce);
        if (!copy) {
            return false;
        }
        safe_free(extranonce_str);
        extranonce_str = copy;
        extranonce_2_len = enonce2_len;
        return true;
    }

    bool set_next_enonce(const char *enonce, int enonce2_len)
    {
        if (!enonce || enonce2_len <= 0 || enonce2_len > MAX_EXTRANONCE_SIZE) {
            return false;
        }
        char *copy = strdup(enonce);
        if (!copy) {
            return false;
        }
        safe_free(next_extranonce_str);
        next_extranonce_str = copy;
        next_extranonce_2_len = enonce2_len;
        return true;
    }

    bool create_job_mining_notify(const mining_notify *notify)
    {
        if (!current_job || !notify || !notify->job_id || !notify->coinbase_1 || !notify->coinbase_2) {
            return false;
        }

        char *job_id = strdup(notify->job_id);
        char *coinbase_1 = strdup(notify->coinbase_1);
        char *coinbase_2 = strdup(notify->coinbase_2);
        if (!job_id || !coinbase_1 || !coinbase_2) {
            free(job_id);
            free(coinbase_1);
            free(coinbase_2);
            return false;
        }

        // do we have a pending extranonce switch?
        if (next_extranonce_str) {
            char *next_copy = strdup(next_extranonce_str);
            if (!next_copy) {
                free(job_id);
                free(coinbase_1);
                free(coinbase_2);
                return false;
            }
            safe_free(extranonce_str);
            extranonce_str = next_copy;
            extranonce_2_len = next_extranonce_2_len;
            safe_free(next_extranonce_str);
            next_extranonce_2_len = 0;
        }

        safe_free(current_job->job_id);
        safe_free(current_job->coinbase_1);
        safe_free(current_job->coinbase_2);

        // copy trivial types
        memcpy(current_job, notify, sizeof(mining_notify));
        current_job->job_id = job_id;
        current_job->coinbase_1 = coinbase_1;
        current_job->coinbase_2 = coinbase_2;

        // set active difficulty with the mining.notify command
        active_stratum_difficulty = stratum_difficulty;
        return true;
    }
};

// Global mining info instances - one per pool slot
static MiningInfoV1 s_miningInfoV1[2] = {MiningInfoV1{}, MiningInfoV1{}};
MiningInfoBase* miningInfo[2] = {&s_miningInfoV1[0], &s_miningInfoV1[1]};

#define min(a, b) ((a < b) ? (a) : (b))
#define max(a, b) ((a > b) ? (a) : (b))

static void create_job_timer(TimerHandle_t xTimer)
{
    pthread_mutex_lock(&job_mutex);
    job_wake_pending = true;
    pthread_cond_signal(&job_cond);
    pthread_mutex_unlock(&job_mutex);
}

void trigger_job_creation()
{
    pthread_mutex_lock(&job_mutex);
    job_wake_pending = true;
    pthread_cond_signal(&job_cond);
    pthread_mutex_unlock(&job_mutex);
}

// Ensure miningInfo[pool] points to the V1 instance.
// Called by all V1 free functions to handle mixed-protocol fallback
// (e.g., pool was SV2 and switched to V1 - miningInfo might still
// point to a V2 instance from create_jobs_sv2.cpp).
static MiningInfoV1* ensureV1(int pool)
{
    if (pool < 0 || pool >= 2) {
        return nullptr;
    }
    if (miningInfo[pool] != &s_miningInfoV1[pool]) {
        // Reset to V1 instance (V2 instances are owned by create_jobs_sv2.cpp)
        s_miningInfoV1[pool].invalidate();
        miningInfo[pool] = &s_miningInfoV1[pool];
    }
    return &s_miningInfoV1[pool];
}

void create_job_set_version_mask(int pool, uint32_t mask)
{
    PThreadGuard g(current_stratum_job_mutex);
    MiningInfoV1 *info = ensureV1(pool);
    if (info) {
        info->set_version_mask(mask);
    }
}

bool create_job_set_difficulty(int pool, uint32_t difficulty)
{
    PThreadGuard g(current_stratum_job_mutex);
    MiningInfoV1 *info = ensureV1(pool);
    return info ? info->set_difficulty(difficulty) : false;
}

void create_job_set_enonce(int pool, char *enonce, int enonce2_len)
{
    PThreadGuard g(current_stratum_job_mutex);
    MiningInfoV1 *info = ensureV1(pool);
    if (!info || !info->set_enonce(enonce, enonce2_len)) {
        ESP_LOGE(TAG, "Rejected invalid/uncopyable extranonce for pool %d", pool);
    }
}

void set_next_enonce(int pool, char *enonce, int enonce2_len)
{
    PThreadGuard g(current_stratum_job_mutex);
    MiningInfoV1 *info = ensureV1(pool);
    if (!info || !info->set_next_enonce(enonce, enonce2_len)) {
        ESP_LOGE(TAG, "Rejected invalid/uncopyable next extranonce for pool %d", pool);
    }
}

void create_job_mining_notify(int pool, mining_notify *notify, bool abandonWork)
{
    if (pool < 0 || pool >= 2) {
        return;
    }
    {
        PThreadGuard g(current_stratum_job_mutex);
        // clear jobs for pool
        if (abandonWork) {
            miningJobGeneration[pool]++;
            clean_asic_jobs_for_pool_locked(pool);
        }
        MiningInfoV1 *info = ensureV1(pool);
        if (!info || !info->create_job_mining_notify(notify)) {
            ESP_LOGE(TAG, "Failed to store mining.notify for pool %d", pool);
            return;
        }
    }
    trigger_job_creation();
}

void create_job_invalidate(int pool)
{
    PThreadGuard g(current_stratum_job_mutex);
    if (pool < 0 || pool >= 2 || !miningInfo[pool]) {
        return;
    }
    miningInfo[pool]->invalidate();
    miningJobGeneration[pool]++;
    clean_asic_jobs_for_pool_locked(pool);
}

void create_jobs_task(void *pvParameters)
{
    Board *board = SYSTEM_MODULE.getBoard();
    Asic *asics = board->getAsics();

    int initial_job_interval = board->getAsicJobIntervalMs();
    TickType_t initial_job_period = pdMS_TO_TICKS(initial_job_interval);
    if (initial_job_interval <= 0 || initial_job_period == 0) {
        ESP_LOGE(TAG, "Invalid ASIC job interval: %dms", initial_job_interval);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "ASIC Job Interval: %d ms", initial_job_interval);
    SYSTEM_MODULE.notifyMiningStarted();
    ESP_LOGI(TAG, "ASIC Ready!");

    // Create the timer
    TimerHandle_t job_timer = xTimerCreate(TAG, initial_job_period, pdTRUE, NULL, create_job_timer);

    if (job_timer == NULL) {
        ESP_LOGE(TAG, "Failed to create timer");
        vTaskDelete(NULL);
        return;
    }

    // Start the timer
    if (xTimerStart(job_timer, 0) != pdPASS) {
        ESP_LOGE(TAG, "Failed to start timer");
        xTimerDelete(job_timer, 0);
        vTaskDelete(NULL);
        return;
    }

    uint32_t last_ntime[2]{0};
    uint64_t last_submit_time = 0;
    uint32_t extranonce_2 = 0;

    // CAN: per-slave rolling counters (upper 7 bits = slave_id, lower 25 = counter)
    uint32_t slave_counters[CAN_SLAVE_MAX] = {0};

    bool has_active_version_mask = false;
    uint32_t active_version_mask = 0;

    int lastJobInterval = initial_job_interval;

    while (1) {
        if (POWER_MANAGEMENT_MODULE.isShutdown()) {
            ESP_LOGW(TAG, "suspended");
            vTaskSuspend(NULL);
        }
        pthread_mutex_lock(&job_mutex);
        while (!job_wake_pending) {
            pthread_cond_wait(&job_cond, &job_mutex);
        }
        job_wake_pending = false;
        pthread_mutex_unlock(&job_mutex);

        // job interval changed via UI
        if (board->getAsicJobIntervalMs() != lastJobInterval) {
            int new_interval = board->getAsicJobIntervalMs();
            TickType_t new_period = pdMS_TO_TICKS(new_interval);
            if (new_interval <= 0 || new_period == 0 || xTimerChangePeriod(job_timer, new_period, 0) != pdPASS) {
                ESP_LOGE(TAG, "Rejected invalid ASIC job interval: %dms", new_interval);
            } else {
                lastJobInterval = new_interval;
            }
        }

        bm_job *next_job = nullptr;
        uint64_t next_job_generation = 0;
        int active_pool = 0;
        const char *active_pool_str = "";

        if (!STRATUM_MANAGER) {
            continue;
        }

        // select pool to mine for
        active_pool = STRATUM_MANAGER->getNextActivePool();
        active_pool_str = active_pool ? "Sec" : "Pri";

        { // scope for mutex
            PThreadGuard g(current_stratum_job_mutex);

            // set current pool data
            MiningInfoBase *mi = miningInfo[active_pool];

            if (!mi->isValid() || !asics) {
                continue;
            }

            if (mi->isNewWork(last_ntime[active_pool])) {
                ESP_LOGI(TAG, "(%s) New Work Received %s", active_pool_str, mi->getJobId());
            }

            uint32_t asic_diff = STRATUM_MANAGER->selectAsicDiff(active_pool, mi->getActiveDifficulty());
            next_job = mi->buildBmJob(extranonce_2, active_pool, asic_diff);
            next_job_generation = miningJobGeneration[active_pool];
        } // mutex

        if (!next_job) {
            ESP_LOGE(TAG, "(%s) Failed to build ASIC job", active_pool_str);
            continue;
        }

        uint8_t asic_job_id = 0;
        {
            // Hold the same lock used by clean/invalidate through hardware send
            // and registry publication. The generation catches changes that
            // happened while the freshly built job was outside the lock.
            PThreadGuard publish_lock(current_stratum_job_mutex);
            if (miningJobGeneration[active_pool] != next_job_generation) {
                ESP_LOGW(TAG, "(%s) Dropping stale ASIC job after clean work", active_pool_str);
                free_bm_job(next_job);
                continue;
            }

            if (!asics->setJobDifficultyMask(next_job->asic_diff)) {
                ESP_LOGE(TAG, "(%s) Dropping ASIC job: difficulty-mask TX failed", active_pool_str);
                free_bm_job(next_job);
                continue;
            }

            if (!has_active_version_mask || active_version_mask != next_job->version_mask) {
                if (!asics->setVersionMask(next_job->version_mask)) {
                    ESP_LOGE(TAG, "(%s) Dropping ASIC job: version-mask TX failed", active_pool_str);
                    free_bm_job(next_job);
                    continue;
                }
                active_version_mask = next_job->version_mask;
                has_active_version_mask = true;
            }

            uint8_t sent_job_id = 0;
            if (!asics->sendWork(extranonce_2, next_job, sent_job_id)) {
                ESP_LOGE(TAG, "(%s) Dropping ASIC job: work TX failed", active_pool_str);
                free_bm_job(next_job);
                continue;
            }
            asic_job_id = sent_job_id;

            uint64_t current_time = esp_timer_get_time();
            if (last_submit_time) {
                ESP_LOGD(TAG, "(%s) job interval %dms", active_pool_str, (int) ((current_time - last_submit_time) / 1e3));
            }
            last_submit_time = current_time;

            ESP_LOGD(TAG, "(%s) Sent Job (%d): %02X", active_pool_str, active_pool, asic_job_id);
            asicJobs.storeJob(next_job, asic_job_id);
            extranonce_2++;
        }

        // --- CAN: send raw job to each slave ---
        for (uint8_t slave = 0; slave < CAN_SLAVE_MAX; slave++) {
            if (!can_master_is_slave_active(slave)) {
                continue;
            }
            uint32_t e2 = can_make_extranonce2(slave, slave_counters[slave]);

            bm_job *slave_job = nullptr;
            uint64_t slave_job_generation = 0;
            {
                PThreadGuard g(current_stratum_job_mutex);
                MiningInfoBase *mi = miningInfo[active_pool];
                if (mi->isValid()) {
                    uint32_t asic_diff = STRATUM_MANAGER->selectAsicDiff(active_pool, mi->getActiveDifficulty());
                    slave_job = mi->buildBmJob(e2, active_pool, asic_diff);
                    slave_job_generation = miningJobGeneration[active_pool];
                }
            }

            if (slave_job) {
                PThreadGuard publish_lock(current_stratum_job_mutex);
                if (miningJobGeneration[active_pool] != slave_job_generation) {
                    ESP_LOGW(TAG, "(%s) Dropping stale CAN job after clean work", active_pool_str);
                    free_bm_job(slave_job);
                    continue;
                }
                // This command is intentionally sent before every job. TWAI
                // queues frames in call order; waiting for the settings enqueue
                // to succeed before queuing the raw job makes a failed mask
                // retry on the next cycle without trusting a master-side cache.
                uint8_t payload[1 + sizeof(slave_job->version_mask)] = {CAN_CMD_SET_VERSION_MASK};
                memcpy(payload + 1, &slave_job->version_mask, sizeof(slave_job->version_mask));
                if (!can_send_settings_cmd(slave, payload, sizeof(payload))) {
                    ESP_LOGE(TAG, "(%s) Dropping CAN job for slave %u: version-mask TX failed",
                             active_pool_str, (unsigned)slave);
                    free_bm_job(slave_job);
                    continue;
                }

                if (!can_send_raw_job(slave, asic_job_id, slave_job)) {
                    ESP_LOGE(TAG, "(%s) Dropping CAN job for slave %u: work TX failed",
                             active_pool_str, (unsigned)slave);
                    free_bm_job(slave_job);
                    continue;
                }
                slaveAsicJobs[slave].storeJob(slave_job, asic_job_id);
                slave_counters[slave]++;
                // slaveAsicJobs owns slave_job now — do not free here
            }
        }

    }

}
