#include <string.h>
#include <math.h>
#include <endian.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "mining_utils.h"
#include "serial.h"
#include "asic.h"
#include "crc.h"


typedef enum
{
    JOB_PACKET = 0,
    CMD_PACKET = 1,
} packet_type_t;


const static char* TAG = "asic";

Asic::Asic() {
    m_current_frequency = 56.25;
    m_actual_current_frequency = m_current_frequency;
    m_asicDifficulty = 0xffffffff;
}

uint16_t Asic::reverseUint16(uint16_t num)
{
    return (num >> 8) | (num << 8);
}

bool Asic::send(uint8_t header, uint8_t *data, uint8_t data_len)
{
    packet_type_t packet_type = (header & TYPE_JOB) ? JOB_PACKET : CMD_PACKET;
    uint8_t total_length = (packet_type == JOB_PACKET) ? (data_len + 6) : (data_len + 5);

    unsigned char buf[total_length];

    // add the preamble
    buf[0] = 0x55;
    buf[1] = 0xAA;

    // add the header field
    buf[2] = header;

    // add the length field
    buf[3] = (packet_type == JOB_PACKET) ? (data_len + 4) : (data_len + 3);

    // add the data
    memcpy(buf + 4, data, data_len);

    // add the correct crc type
    if (packet_type == JOB_PACKET) {
        uint16_t crc16_total = crc16_false(buf + 2, data_len + 2);
        buf[4 + data_len] = (crc16_total >> 8) & 0xFF;
        buf[5 + data_len] = crc16_total & 0xFF;
    } else {
        buf[4 + data_len] = crc5(buf + 2, data_len + 2);
    }

    // send serial data
    const int written = SERIAL_send(buf, total_length);
    if (written < 0) {
        ESP_LOGE(TAG, "ASIC UART TX failed: %d (expected %u bytes)",
                 written, (unsigned)total_length);
        return false;
    }
    if (written != total_length) {
        ESP_LOGE(TAG, "ASIC UART TX short write: %d/%u bytes",
                 written, (unsigned)total_length);
        return false;
    }
    return true;
}

bool Asic::send6(uint8_t header, uint8_t b0, uint8_t b1, uint8_t b2, uint8_t b3, uint8_t b4, uint8_t b5) {
    uint8_t buf[6] = {b0, b1, b2, b3, b4, b5};
    return send(header, buf, sizeof(buf));
}

bool Asic::send2(uint8_t header, uint8_t b0, uint8_t b1) {
    uint8_t buf[2] = {b0, b1};
    return send(header, buf, sizeof(buf));
}

bool Asic::sendChainInactive(void)
{
    return send2(TYPE_CMD | GROUP_ALL | CMD_INACTIVE, 0x00, 0x00);
}

bool Asic::setChipAddress(uint8_t chipAddr)
{
    return send2(TYPE_CMD | GROUP_SINGLE | CMD_SETADDRESS, chipAddr, 0x00);
}

void Asic::sendReadAddress(void)
{
    send2(TYPE_CMD | GROUP_ALL | CMD_READ, 0x00, 0x00);
}

// Function to set the hash frequency
// gives the same PLL settings as the S21 dumps
bool Asic::sendHashFrequency(float target_freq) {
    if (!isfinite(target_freq) || target_freq <= 0.0f) {
        ESP_LOGE(TAG, "Invalid target frequency: %.2fMHz", target_freq);
        return false;
    }

    // Qualified BM1368 low-VCO point. Keep the cached control frequency at
    // the nominal target so PLL ramping and governor comparisons converge on
    // 540MHz, while reporting the physical PLL output separately.
    constexpr float BM1368_540_NOMINAL_MHZ = 540.0f;
    constexpr float BM1368_540_ACTUAL_MHZ = 540.625f;
    constexpr float PLL_TARGET_EPSILON_MHZ = 0.001f;
    if (strcmp(getName(), "BM1368") == 0 &&
        fabsf(target_freq - BM1368_540_NOMINAL_MHZ) <= PLL_TARGET_EPSILON_MHZ) {
        uint8_t freqbuf[6] = {0x00, 0x08, 0x40, 0xAD, 0x02, 0x30};
        if (!send(CMD_WRITE_ALL, freqbuf, sizeof(freqbuf))) {
            ESP_LOGE(TAG, "Failed to send BM1368 low-VCO PLL settings for 540MHz");
            return false;
        }

        ESP_LOGI(TAG, "Setting BM1368 Frequency to 540.00MHz "
                      "(540.625MHz actual, low-VCO)");
        m_current_frequency = BM1368_540_NOMINAL_MHZ;
        m_actual_current_frequency = BM1368_540_ACTUAL_MHZ;
        return true;
    }

    float min_diff = 2.0;
    uint8_t freqbuf[6] = {0x00, 0x08, 0x40, 0xA0, 0x02, 0x41};
    int postdiv_min = 255;
    int postdiv2_min = 255;
    int refdiv, fb_divider, postdiv1, postdiv2;
    float newf;
    bool found = false;
    int best_refdiv, best_fb_divider, best_postdiv1, best_postdiv2;
    float best_newf;

    for (refdiv = 2; refdiv > 0; refdiv--) {
        for (postdiv1 = 7; postdiv1 > 0; postdiv1--) {
            for (postdiv2 = 7; postdiv2 > 0; postdiv2--) {
                fb_divider = (int)round(target_freq / 25.0 * (refdiv * postdiv2 * postdiv1));
                newf = 25.0 * fb_divider / (refdiv * postdiv2 * postdiv1);
                if (
                    fb_divider >= 0xa0 && fb_divider <= 0xef &&
                    fabs(target_freq - newf) <= min_diff &&
                    postdiv1 >= postdiv2 &&
                    postdiv1 * postdiv2 < postdiv_min &&
                    postdiv2 <= postdiv2_min
                ) {
                    postdiv2_min = postdiv2;
                    postdiv_min = postdiv1 * postdiv2;
                    best_refdiv = refdiv;
                    best_fb_divider = fb_divider;
                    best_postdiv1 = postdiv1;
                    best_postdiv2 = postdiv2;
                    best_newf = newf;
                    min_diff  = fabs(target_freq - newf);
                    found = true;
                }
            }
        }
    }

    if (!found) {
        ESP_LOGE(TAG, "Didn't find PLL settings for target frequency %.2f (error: %.2fMHZ)", target_freq, min_diff);
        return false;
    }

    freqbuf[2] = (best_fb_divider * 25 / best_refdiv >= 2400) ? 0x50 : 0x40;
    freqbuf[3] = best_fb_divider;
    freqbuf[4] = best_refdiv;
    freqbuf[5] = (((best_postdiv1 - 1) & 0xf) << 4) | ((best_postdiv2 - 1) & 0xf);

    if (!send(CMD_WRITE_ALL, freqbuf, sizeof(freqbuf))) {
        ESP_LOGE(TAG, "Failed to send PLL settings for %.2fMHz", target_freq);
        return false;
    }
    //ESP_LOG_BUFFER_HEX(TAG, freqbuf, sizeof(freqbuf));

    ESP_LOGI(TAG, "Setting Frequency to %.2fMHz (%.2f) (error: %.2fMHZ)", target_freq, best_newf, min_diff);
    m_current_frequency = target_freq;
    m_actual_current_frequency = best_newf;
    return true;
}

int Asic::setMaxBaud(void)
{
//    return 115749;
    ESP_LOGI(TAG, "Setting max baud of 1000000 ");
    if (!send6(CMD_WRITE_ALL, 0x00, 0x28, 0x11, 0x30, 0x02, 0x00)) {
        ESP_LOGE(TAG, "Failed to send max-baud command");
        return 0;
    }
    return 1000000;
}

// set version rolling frequency
constexpr uint32_t ASIC_IO_CLK_HZ_U32 = 15'000'000u; // 15 MHz
constexpr uint32_t VR_TICK_DIV_U32    = 5000u;       // VR counter increments every 5000 IO clock cycles
constexpr uint32_t VR_TICK_HZ_U32     = ASIC_IO_CLK_HZ_U32 / VR_TICK_DIV_U32; // 3000 Hz
constexpr uint64_t VR_REG_PER_HZ_U64  = 65536ull * VR_TICK_HZ_U32;            // 196,608,000

// Version rolling frequency register @0x10 (MSB -> LSB)
bool Asic::setVrFreqReg(uint32_t value) {
    ESP_LOGI(TAG, "setting 0x10 to %08lx", value);
    return send6(CMD_WRITE_ALL, 0x00, 0x10,
                 static_cast<uint8_t>((value >> 24) & 0xFF),
                 static_cast<uint8_t>((value >> 16) & 0xFF),
                 static_cast<uint8_t>((value >>  8) & 0xFF),
                 static_cast<uint8_t>((value >>  0) & 0xFF));
}

// Convert desired VR frequency (Hz, integer) to register value for 0x10
uint32_t Asic::vrFreqToReg(uint32_t freq_hz) {
    if (freq_hz == 0) {
        ESP_LOGW(TAG, "invalid version rolling frequency: 0Hz");
        return 0;
    }

    // reg = round(VR_REG_PER_HZ / freq_hz) using integer division with rounding
    return static_cast<uint32_t>((VR_REG_PER_HZ_U64 + (freq_hz / 2)) / freq_hz);
}

// Convert 0x10 register value back to VR frequency (Hz, integer)
uint32_t Asic::vrRegToFreq(uint32_t reg) {
    if (reg == 0) {
        ESP_LOGW(TAG, "invalid version rolling register: 0");
        return 0;
    }

    // freq = round(VR_REG_PER_HZ / reg) using integer division with rounding
    return static_cast<uint32_t>((VR_REG_PER_HZ_U64 + (reg / 2)) / reg);
}

bool Asic::setVrFrequency(uint32_t freq_hz) {
    if (freq_hz == 0) {
        ESP_LOGW(TAG, "ignoring invalid version rolling frequency: 0Hz");
        return false;
    }

    return setVrFreqReg(vrFreqToReg(freq_hz));
}

bool Asic::setVersionMask(uint32_t version_mask) {
    uint16_t chip_mask = static_cast<uint16_t>((version_mask >> 13) & 0xFFFFu);
    ESP_LOGI(TAG, "setting version rolling mask %08lx (chip mask %04x)",
             (unsigned long) version_mask, (unsigned int) chip_mask);
    return send6(CMD_WRITE_ALL, 0x00, 0xA4, 0x90, 0x00,
                 static_cast<uint8_t>((chip_mask >> 8) & 0xFF),
                 static_cast<uint8_t>(chip_mask & 0xFF));
}

// default calculation using address_interval
uint8_t Asic::chipIndexFromAddr(uint8_t addr) {
    return (m_addressInterval > 0) ? (addr / m_addressInterval) : 0;
}

uint8_t Asic::addrFromChipIndex(uint8_t idx) {
    return idx * m_addressInterval;
}

int Asic::nonceToAsic(uint32_t nonce) {
    uint32_t nonce_h = __bswap32(nonce);
    return (m_addressInterval > 0) ? ((uint8_t)((nonce_h >> 17) & 0xff) / m_addressInterval) : 0;
}

void Asic::requestChipTemp() {
    // NOP
}

void Asic::resetCounter(uint8_t reg) {
    send6(CMD_WRITE_ALL, 0x00, reg, 0x00, 0x00, 0x00, 0x00);
}

void Asic::readCounter(uint8_t reg) {
    send2(CMD_READ_ALL, 0x00, reg);
}


// Function to perform frequency transition up or down
bool Asic::doFrequencyTransition(float target_frequency) {
    if (!isfinite(target_frequency) || target_frequency <= 0.0f) {
        ESP_LOGE(TAG, "Invalid frequency transition target: %.2fMHz", target_frequency);
        return false;
    }

    bool pllCommandSent = false;
    while (fabsf(m_current_frequency - target_frequency) > 0.001f) {
        if (!stepAsicFrequency(target_frequency)) {
            ESP_LOGE(TAG, "Failed PLL transition toward %.2fMHz", target_frequency);
            return false;
        }
        pllCommandSent = true;
        if (fabsf(m_current_frequency - target_frequency) > 0.001f) {
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }

    // BM1368 initialization writes the version-rolling registers immediately
    // after this blocking ramp. Preserve a full settle interval after the last
    // PLL command as well as between steps, otherwise a cold boot can start the
    // hashing cores in a degraded state even though the requested clock is
    // cached/reported correctly. Runtime governor steps use stepAsicFrequency() directly
    // and therefore remain non-blocking.
    if (pllCommandSent) {
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    return true;
}

bool Asic::stepAsicFrequency(float target_frequency, float max_step_mhz)
{
    if (!isfinite(target_frequency) || target_frequency <= 0.0f ||
        !isfinite(max_step_mhz) || max_step_mhz <= 0.0f) {
        ESP_LOGE(TAG, "Invalid PLL step target=%.2fMHz step=%.2fMHz", target_frequency, max_step_mhz);
        return false;
    }

    const float delta = target_frequency - m_current_frequency;
    if (fabsf(delta) <= 0.001f) {
        return true;
    }

    float next_frequency = target_frequency;
    const float remainder = fmodf(m_current_frequency, max_step_mhz);
    const bool aligned = fabsf(remainder) <= 0.001f || fabsf(remainder - max_step_mhz) <= 0.001f;
    if (!aligned) {
        // Join the PLL step grid first. Do not overshoot a nearby final target.
        const float grid_frequency = delta > 0.0f
            ? ceilf(m_current_frequency / max_step_mhz) * max_step_mhz
            : floorf(m_current_frequency / max_step_mhz) * max_step_mhz;
        if ((delta > 0.0f && grid_frequency < target_frequency) ||
            (delta < 0.0f && grid_frequency > target_frequency)) {
            next_frequency = grid_frequency;
        }
    } else if (fabsf(delta) > max_step_mhz) {
        next_frequency = m_current_frequency + copysignf(max_step_mhz, delta);
    }
    return sendHashFrequency(next_frequency);
}

int Asic::count_asics() {

    // read register 00 on all chips (should respond AA 55 13 68 00 00 00 00 00 00 0F)
    if (!send2(CMD_READ_ALL, 0x00, 0x00)) {
        ESP_LOGE(TAG, "Failed to request ASIC addresses");
        return 0;
    }

    uint8_t buf[11];
    int chip_counter = 0;
    int received = 0;
    while ((received = SERIAL_rx(buf, sizeof(buf), 1000)) > 0) {
        if (received != static_cast<int>(sizeof(buf))) {
            ESP_LOGE(TAG, "Incomplete ASIC address response: %d/%u", received, (unsigned)sizeof(buf));
            SERIAL_clear_buffer();
            break;
        }
//        ESP_LOG_BUFFER_HEX(TAG, buf, sizeof(buf));
        if (memcmp(getChipId(), buf, 6) == 0) {
            chip_counter++;
            ESP_LOGI(TAG, "found asic #%d", chip_counter);
        } else {
            ESP_LOGE(TAG, "unexpected response ... ignoring ...");
            ESP_LOG_BUFFER_HEX(TAG, buf, sizeof(buf));
        }
    }
    return chip_counter;
}

bool Asic::setJobDifficultyMask(int difficulty)
{
    // Default mask of 256 diff
    unsigned char job_difficulty_mask[9] = {0x00, TICKET_MASK, 0b00000000, 0b00000000, 0b00000000, 0b11111111};

    // The mask must be a power of 2 so there are no holes
    // Correct:  {0b00000000, 0b00000000, 0b11111111, 0b11111111}
    // Incorrect: {0b00000000, 0b00000000, 0b11100111, 0b11111111}
    // (difficulty - 1) if it is a pow 2 then step down to second largest for more hashrate sampling
    difficulty = _largest_power_of_two(difficulty) - 1;

    if (m_asicDifficulty == difficulty) {
        return true;
    }

    // convert difficulty into char array
    // Ex: 256 = {0b00000000, 0b00000000, 0b00000000, 0b11111111}, {0x00, 0x00, 0x00, 0xff}
    // Ex: 512 = {0b00000000, 0b00000000, 0b00000001, 0b11111111}, {0x00, 0x00, 0x01, 0xff}
    for (int i = 0; i < 4; i++) {
        char value = (difficulty >> (8 * i)) & 0xFF;
        // The char is read in backwards to the register so we need to reverse them
        // So a mask of 512 looks like 0b00000000 00000000 00000001 1111111
        // and not 0b00000000 00000000 10000000 1111111

        job_difficulty_mask[5 - i] = _reverse_bits(value);
    }

    ESP_LOGI(TAG, "Setting ASIC difficulty mask to %d", difficulty);

    if (!send((CMD_WRITE_ALL), job_difficulty_mask, 6)) {
        ESP_LOGE(TAG, "Failed to send ASIC difficulty mask");
        return false;
    }

    // remember the hw difficulty
    m_asicDifficulty = difficulty;
    return true;
}

// can ramp up and down in 6.25MHz steps
bool Asic::setAsicFrequency(float target_freq) {
    return doFrequencyTransition(target_freq);
}


bool Asic::sendWork(uint32_t job_id, bm_job *next_bm_job, uint8_t &asic_job_id)
{
    if (!next_bm_job) {
        ESP_LOGE(TAG, "Cannot send a null ASIC job");
        return false;
    }

    BM1368_job job;

    job.job_id = jobToAsicId(job_id);

    job.num_midstates = 0x01;
    memcpy(&job.starting_nonce, &next_bm_job->starting_nonce, 4);
    memcpy(&job.nbits, &next_bm_job->target, 4);
    memcpy(&job.ntime, &next_bm_job->ntime, 4);
    memcpy(job.merkle_root, next_bm_job->merkle_root_be, 32);
    memcpy(job.prev_block_hash, next_bm_job->prev_block_hash_be, 32);
    memcpy(&job.version, &next_bm_job->version, 4);

    if (!send((TYPE_JOB | GROUP_SINGLE | CMD_WRITE), (uint8_t*) &job, sizeof(BM1368_job))) {
        ESP_LOGE(TAG, "Failed to send ASIC job %02X", job.job_id);
        return false;
    }

    // Return it through an out-parameter because different ASICs calculate it
    // differently and every uint8_t value is a valid job ID.
    asic_job_id = job.job_id;
    return true;
}

bool Asic::sendRawJob(BM1368_job *job)
{
    if (!job) {
        ESP_LOGE(TAG, "Cannot send a null raw ASIC job");
        return false;
    }
    return send((TYPE_JOB | GROUP_SINGLE | CMD_WRITE), (uint8_t*) job, sizeof(BM1368_job));
}

bool Asic::receiveWork(asic_result_t *result)
{
    // wait for a response, wait time is pretty arbitrary
    int received = SERIAL_rx((uint8_t*) result, 11, 60000);

    if (received < 0) {
        ESP_LOGI(TAG, "Error in serial RX");
        return false;
    } else if (received == 0) {
        // Didn't find a solution, restart and try again
        return false;
    }

    if (received != static_cast<int>(sizeof(*result))) {
        ESP_LOGE(TAG, "Incomplete serial RX frame: %d/%u", received, (unsigned)sizeof(*result));
        ESP_LOG_BUFFER_HEX(TAG, (uint8_t*) result, received);
        SERIAL_clear_buffer();
        return false;
    }

    if (result->preamble[0] != 0xAA || result->preamble[1] != 0x55) {
        ESP_LOGE(TAG, "Serial RX invalid %i", received);
        ESP_LOG_BUFFER_HEX(TAG, (uint8_t*) result, received);
        SERIAL_clear_buffer();
        return false;
    }

    return true;
}


bool Asic::processWork(task_result *result)
{
    asic_result_t asic_result;
    if (!receiveWork(&asic_result)) {
        return false;
    }

    if (!(asic_result.crc & 0x80)) {
        result->data = __bswap32(asic_result.nonce);
        result->reg = asic_result.job_id;
        result->is_reg_resp = 1;
        result->asic_nr = chipIndexFromAddr(asic_result.midstate_num);
        return true;
    }

    uint8_t job_id = asicToJobId(asic_result.job_id);

    uint32_t rolled_version = (reverseUint16(asic_result.version) << 13); // shift the 16 bit value left 13

    int asic_nr = nonceToAsic(asic_result.nonce);

    result->job_id = job_id;
    result->asic_nr = asic_nr;
    result->nonce = asic_result.nonce;
    result->rolled_version = rolled_version;
    result->is_reg_resp = 0;
    return true;
}
