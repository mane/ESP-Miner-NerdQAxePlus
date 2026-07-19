#pragma once
#include <stdint.h>

void ASIC_result_task(void *pvParameters);
uint64_t getDuplicateHWNonces();
uint64_t getShareQueueDrops();
