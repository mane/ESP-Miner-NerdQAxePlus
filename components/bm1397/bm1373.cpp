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
#include "bm1373.h"

#include "crc.h"
#include "serial.h"
#include "mining_utils.h"


static const char *TAG = "bm1373Module";

static const uint8_t chip_id[6] = {0xaa, 0x55, 0x13, 0x72, 0x00, 0x00};

static const uint64_t BM1373_CORE_COUNT = 128; // TODO
static const uint64_t BM1373_SMALL_CORE_COUNT = 6860; // TODO

#define REG_NONCE_TOTAL_CNT 0x8c

BM1373::BM1373() : BM1370() {
    // NOP
}

const uint8_t* BM1373::getChipId() {
    return (uint8_t*) chip_id;
}

uint32_t BM1373::getDefaultVrFrequency() {
    return vrRegToFreq(0x1eb5);
};

uint8_t BM1373::init(uint64_t frequency, uint16_t asic_count, uint32_t difficulty, uint32_t vrFrequency)
{
    // reset is done externally to not have board dependencies

    // enable and set default version rolling mask
    setVersionMask(ASIC_DEFAULT_VERSION_MASK);

    // enable and set default version rolling mask (again)
    setVersionMask(ASIC_DEFAULT_VERSION_MASK);

    // enable and set default version rolling mask (again)
    setVersionMask(ASIC_DEFAULT_VERSION_MASK);

    // enable and set default version rolling mask (again)
    setVersionMask(ASIC_DEFAULT_VERSION_MASK);

    int chip_counter = count_asics();
    ESP_LOGIE(chip_counter == asic_count, TAG, "%i chip(s) detected on the chain, expected %i", chip_counter, asic_count);

    // enable and set default version rolling mask (again)
    setVersionMask(ASIC_DEFAULT_VERSION_MASK);

    // Reg_A8
    send6(CMD_WRITE_ALL, 0x00, 0xA8, 0x00, 0x07, 0x00, 0x00);

    // Misc Control
    send6(CMD_WRITE_ALL, 0x00, 0x18, 0xFf, 0x00, 0xC1, 0x00);

    // chain inactive
    sendChainInactive();

    // set chip address
    m_addressInterval = 8;
    for (uint8_t i = 0; i < chip_counter; i++) {
        setChipAddress(i * m_addressInterval);
    }

    // Core Register Control
    send6(CMD_WRITE_ALL, 0x00, 0x3C, 0x80, 0x00, 0x8B, 0x00);

    // Core Register Control
    send6(CMD_WRITE_ALL, 0x00, 0x3C, 0x80, 0x00, 0x80, 0x0C);

    setJobDifficultyMask(difficulty);

    // Set the IO Driver Strength on chip 00
    send6(CMD_WRITE_ALL, 0x00, 0x58, 0x00, 0x01, 0x11, 0x11);

    // ?
    send6(CMD_WRITE_ALL, 0x00, 0x68, 0x5A, 0xA5, 0x5A, 0xA5);

    for (uint8_t i = 0; i < chip_counter; i++) {
        uint8_t addr = i * m_addressInterval;
        // Reg_A8
        send6(CMD_WRITE_SINGLE, addr, 0xA8, 0x00, 0x07, 0x01, 0xF0);
        // Misc Control
        send6(CMD_WRITE_SINGLE, addr, 0x18, 0xFF, 0x00, 0xC1, 0x00);
        // Core Register Control
        send6(CMD_WRITE_SINGLE, addr, 0x3C, 0x80, 0x00, 0x8B, 0x00);
        // Core Register Control
        send6(CMD_WRITE_SINGLE, addr, 0x3C, 0x80, 0x00, 0x80, 0x0c);
        // Core Register Control
        send6(CMD_WRITE_SINGLE, addr, 0x3C, 0x80, 0x00, 0x82, 0xAA);
    }

    // ?
    send6(CMD_WRITE_ALL, 0x00, 0xB9, 0x00, 0x00, 0x44, 0x80);

    // Analog Mux Control
    send6(CMD_WRITE_ALL, 0x00, 0x54, 0x00, 0x00, 0x00, 0x02);

    // ?
    send6(CMD_WRITE_ALL, 0x00, 0xB9, 0x00, 0x00, 0x44, 0x80);

    // Core Register Control
    send6(CMD_WRITE_ALL, 0x00, 0x3C, 0x80, 0x00, 0x8D, 0xEE);

    doFrequencyTransition(frequency);

    // set 0x10
    setVrFrequency(vrFrequency);

    setVersionMask(ASIC_DEFAULT_VERSION_MASK);

    return chip_counter;
}

uint16_t BM1373::getSmallCoreCount() {
    return BM1373_SMALL_CORE_COUNT;
}

int BM1373::nonceToAsic(uint32_t nonce) {
    // TODO: verify shift and mask with different chip counts
    uint32_t nonce_h = __bswap32(nonce);
    return (nonce_h >> 24) & 0x03;
}
