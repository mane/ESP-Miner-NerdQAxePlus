#pragma once

#include <pthread.h>
#include "boards/board.h"
#include "fan_controller.h"
#include "hashrate_governor.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_timer.h"


template <class T>
class LockGuard {
public:
    LockGuard(T& obj) : m_obj(obj) { m_obj.lock(); }
    ~LockGuard() { m_obj.unlock(); }
private:
    T& m_obj;
};

class PowerManagementTask {
  protected:
    pthread_mutex_t m_loop_mutex = PTHREAD_MUTEX_INITIALIZER;
    pthread_cond_t m_loop_cond = PTHREAD_COND_INITIALIZER;
    bool m_loopPending = false;

    SemaphoreHandle_t m_mutex;
    TimerHandle_t m_timer;

    char m_logBuffer[256]{};
    float m_chipTempMax = 0;
    float m_vrTemp = 0;
    float m_vrTempInt = 0;
    float m_voltage = 0;
    float m_power = 0;
    float m_current = 0;
    bool m_shutdown = false;
    FanController m_fanController;
    Board* m_board = nullptr;

    HashrateGovernor::Governor m_hashrateGovernor;
    HashrateGovernor::Reason m_governorReason = HashrateGovernor::Reason::DISABLED;
    HashrateGovernor::State m_governorState = HashrateGovernor::State::DISABLED;
    uint64_t m_lastTelemetryMs = 0;
    uint16_t m_runtimeFrequencyTarget = 0;
    uint16_t m_appliedCoreVoltageMillis = 0;
    uint16_t m_governorBaseFrequency = 0;
    uint16_t m_governorMaxFrequency = 0;
    uint16_t m_governorPowerLimit10 = 0;
    bool m_governorEnabled = false;
    bool m_governorConfigured = false;
    bool m_governorEmergency = false;
    float m_governorUtilization = 0.0f;

    void checkVrFrequencyChanged();
    void readAndPublishPowerTelemetry();
    void syncHashrateGovernorConfiguration(uint64_t nowMs);
    void updateHashrateGovernor(uint64_t nowMs);
    void applyRuntimeAsicSettings();
    const char *governorStateString() const;
    void task();

    bool startTimer();
    void trigger();

    void logChipTemps();
    void requestChipTemps();

  public:
    struct HashrateGovernorStatus {
        bool enabled = false;
        uint16_t targetFrequency = 0;
        uint16_t lastStableFrequency = 0;
        float utilization = 0.0f;
        const char *state = "disabled";
        const char *lastReason = "disabled";
    };

    PowerManagementTask();

    // synchronized rebooting to now mess up i2c comms
    void restart();

    static void taskWrapper(void *pvParameters);
    static void create_job_timer(TimerHandle_t xTimer);

    float getPower()
    {
        return m_power;
    };
    float getVoltage()
    {
        return m_voltage;
    };
    float getCurrent()
    {
        return m_current;
    };
    float getChipTempMax()
    {
        return m_chipTempMax;
    };
    float getVRTemp()
    {
        return m_vrTemp;
    };
    float getVRTempInt()
    {
        return m_vrTempInt;
    }

    uint16_t getFanRPM(int channel);

    // Copies a coherent API snapshot under the task's recursive mutex.
    void copyHashrateGovernorStatus(HashrateGovernorStatus *status);

    uint16_t getFanPerc(int ch = 0)
    {
        return m_fanController.getSpeedPerc(ch);
    };

    FanController& getFanController()
    {
        return m_fanController;
    }

    void lock() {
        xSemaphoreTakeRecursive(m_mutex, portMAX_DELAY);
    }

    void unlock() {
        xSemaphoreGiveRecursive(m_mutex);
    }

    void shutdown();

    bool isShutdown() {
        return m_shutdown;
    }
};
