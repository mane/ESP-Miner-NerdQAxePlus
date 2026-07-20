#include <endian.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stddef.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "asic.h"
#include "bm1368.h"

#include "crc.h"
#include "serial.h"
#include "mining_utils.h"

static const uint64_t BM1368_CORE_COUNT = 80;
static const uint64_t BM1368_SMALL_CORE_COUNT = 1276;

static const char *TAG = "bm1368Module";

static const uint8_t chip_id[6] = {0xaa, 0x55, 0x13, 0x68, 0x00, 0x00};

BM1368::BM1368() : Asic() {
    //NOP
}

const uint8_t* BM1368::getChipId() {
    return (uint8_t*) chip_id;
}

uint32_t BM1368::getDefaultVrFrequency() {
    return vrRegToFreq(0x15a4);
};


uint8_t BM1368::init(uint64_t frequency, uint16_t asic_count, uint32_t difficulty, uint32_t vrFrequency)
{
    // reset is done externally to not have board dependencies

    // Enable and set the default version rolling mask. Repeated writes are
    // required by the BM1368 cold-start sequence.
    if (!setVersionMask(ASIC_DEFAULT_VERSION_MASK) ||
        !setVersionMask(ASIC_DEFAULT_VERSION_MASK) ||
        !setVersionMask(ASIC_DEFAULT_VERSION_MASK) ||
        !setVersionMask(ASIC_DEFAULT_VERSION_MASK)) {
        ESP_LOGE(TAG, "Failed initial version-mask sequence");
        return 0;
    }

    int chip_counter = count_asics();
    ESP_LOGIE(chip_counter == asic_count, TAG, "%i chip(s) detected on the chain, expected %i", chip_counter, asic_count);

    // enable and set default version rolling mask (again)
    if (!setVersionMask(ASIC_DEFAULT_VERSION_MASK)) {
        ESP_LOGE(TAG, "Failed post-detection version-mask write");
        return 0;
    }

    // Reg_A8
    if (!send6(CMD_WRITE_ALL, 0x00, 0xA8, 0x00, 0x07, 0x00, 0x00) ||
        // Misc Control
        !send6(CMD_WRITE_ALL, 0x00, 0x18, 0xFF, 0x0F, 0xC1, 0x00) ||
        // chain inactive
        !sendChainInactive()) {
        ESP_LOGE(TAG, "Failed broadcast pre-address initialization");
        return 0;
    }

    // set chip address - distribute evenly across 0-255 range
    m_addressInterval = (chip_counter > 0) ? (256 / next_power_of_two(chip_counter)) : 2;
    for (uint8_t i = 0; i < chip_counter; i++) {
        if (!setChipAddress(i * m_addressInterval)) {
            ESP_LOGE(TAG, "Failed to set address for ASIC %u", (unsigned)i);
            return 0;
        }
    }

    // Core Register Control
    if (!send6(CMD_WRITE_ALL, 0x00, 0x3C, 0x80, 0x00, 0x8B, 0x00) ||
        // Core Register Control
        !send6(CMD_WRITE_ALL, 0x00, 0x3C, 0x80, 0x00, 0x80, 0x18) ||
        !setJobDifficultyMask(difficulty) ||
        // Analog Mux Control
        !send6(CMD_WRITE_ALL, 0x00, 0x54, 0x00, 0x00, 0x00, 0x03) ||
        // Set the IO Driver Strength on chip 00
        !send6(CMD_WRITE_ALL, 0x00, 0x58, 0x02, 0x11, 0x11, 0x11)) {
        ESP_LOGE(TAG, "Failed broadcast core initialization");
        return 0;
    }

    for (uint8_t i = 0; i < chip_counter; i++) {
        uint8_t addr = i * m_addressInterval;
        if (
            // Reg_A8
            !send6(CMD_WRITE_SINGLE, addr, 0xA8, 0x00, 0x07, 0x01, 0xF0) ||
            // Misc Control
            !send6(CMD_WRITE_SINGLE, addr, 0x18, 0xF0, 0x00, 0xC1, 0x00) ||
            // Core Register Control
            !send6(CMD_WRITE_SINGLE, addr, 0x3C, 0x80, 0x00, 0x8B, 0x00) ||
            // Core Register Control
            !send6(CMD_WRITE_SINGLE, addr, 0x3C, 0x80, 0x00, 0x80, 0x18) ||
            // Core Register Control
            !send6(CMD_WRITE_SINGLE, addr, 0x3C, 0x80, 0x00, 0x82, 0xAA)) {
            ESP_LOGE(TAG, "Failed register initialization for ASIC %u", (unsigned)i);
            return 0;
        }
    }

    if (!doFrequencyTransition(frequency)) {
        ESP_LOGE(TAG, "Failed to initialize ASIC frequency to %lluMHz",
                 (unsigned long long)frequency);
        return 0;
    }

    // set 0x10 and restore the default version mask
    if (!setVrFrequency(vrFrequency) || !setVersionMask(ASIC_DEFAULT_VERSION_MASK)) {
        ESP_LOGE(TAG, "Failed final version-rolling configuration");
        return 0;
    }

    return chip_counter;
}

void BM1368::requestChipTemp() {
    send2(CMD_READ_ALL, 0x00, 0xB4);
    send6(CMD_WRITE_ALL, 0x00, 0xB0, 0x80, 0x00, 0x00, 0x00);
    send6(CMD_WRITE_ALL, 0x00, 0xB0, 0x00, 0x02, 0x00, 0x00);
    send6(CMD_WRITE_ALL, 0x00, 0xB0, 0x01, 0x02, 0x00, 0x00);
    send6(CMD_WRITE_ALL, 0x00, 0xB0, 0x10, 0x02, 0x00, 0x00);
}

uint8_t BM1368::jobToAsicId(uint8_t job_id) {
    // job-IDs: 00, 18, 30, 48, 60, 78, 10, 28, 40, 58, 70, 08, 20, 38, 50, 68
    return (job_id * 24) & 0x7f;
}

uint8_t BM1368::asicToJobId(uint8_t asic_id) {
    return (asic_id & 0xf0) >> 1;
}

uint16_t BM1368::getSmallCoreCount() {
    return BM1368_SMALL_CORE_COUNT;
}
