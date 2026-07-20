idf_component_register(
SRCS
    "main.cpp"
    "nvs_config.cpp"
    "system.cpp"
    "sntp.cpp"
    "utils.cpp"
    "boards/board.cpp"
    "boards/nerdqaxeplus.cpp"
    "boards/drivers/EMC2302.cpp"
    "boards/drivers/TMP1075.cpp"
    "boards/drivers/TPS53647.cpp"
    "boards/drivers/i2c_master.cpp"
    "boards/drivers/fxl6408.cpp"
    "history.cpp"
    "discord.cpp"
    "fan_config_safety.cpp"
    "fan_controller.cpp"
    "hashrate_governor.cpp"
    "./pid/PID_v1_bc.cpp"
    "./pid/pid_timer.cpp"
    "./http_server/http_server.cpp"
    "./http_server/http_cors.cpp"
    "./http_server/http_utils.cpp"
    "./http_server/http_websocket.cpp"
    "./http_server/fan_settings_patch.cpp"
    "./http_server/handler_influx.cpp"
    "./http_server/handler_alert.cpp"
    "./http_server/handler_can_swarm.cpp"
    "./http_server/handler_system.cpp"
    "./http_server/handler_ota.cpp"
    "./http_server/ota_operation.cpp"
    "./http_server/www_image_identity.cpp"
    "./http_server/handler_otp.cpp"
    "./http_server/handler_restart.cpp"
    "./http_server/handler_shutdown.cpp"
    "./http_server/handler_wifi_scan.cpp"
    "./http_server/handler_file.cpp"
    "./http_server/handler_ota_factory.cpp"
    "./http_server/v2/handler_v2_dashboard.cpp"
    "./http_server/v2/handler_v2_settings.cpp"
    "./http_server/v2/handler_v2_identify.cpp"
    "./http_server/v2/handler_v2_system.cpp"
    "./self_test/self_test.cpp"
    "./stratum/stratum_api.cpp"
    "./stratum/stratum_transport.cpp"
    "./stratum/stratum_config.cpp"
    "./stratum/stratum_task.cpp"
    "./stratum/stratum_manager.cpp"
    "./stratum/stratum_manager_fallback.cpp"
    "./stratum/stratum_manager_dual_pool.cpp"
    "./stratum/stratum_transport_noise.cpp"
    "./stratum/stratum_task_v2.cpp"
    "./network/connect.cpp"
    "./network/mdns_service.cpp"
    "./network/network_manager.cpp"
    "./network/w5500.cpp"
    "./tasks/create_jobs_task.cpp"
    "./tasks/create_jobs_sv2.cpp"
    "./tasks/mining_info_v2.cpp"
    "./tasks/asic_result_task.cpp"
    "./tasks/influx_task.cpp"
    "./tasks/ping_task.cpp"
    "./tasks/power_management_task.cpp"
    "./tasks/hashrate_monitor_task.cpp"
    "./tasks/apis_task.cpp"
    "./tasks/wifi_health.cpp"
    "./tasks/can_task.cpp"
    "./tasks/can_sender.cpp"
    "./tasks/can_slave_task.cpp"
    "./tasks/can_master_task.cpp"
    "./displays/displayDriver.cpp"
    "./displays/ui.cpp"
    "./displays/ui_ipc.cpp"
    "./displays/button.cpp"
    "./displays/ui_helpers.cpp"
    "./displays/images/ui_font_DigitalNumbers16.c"
    "./displays/images/ui_font_DigitalNumbers28.c"
    "./displays/images/ui_font_OpenSansBold13.c"
    "./displays/images/ui_font_OpenSansBold14.c"
    "./displays/images/ui_font_OpenSansBold24.c"
    "./displays/images/ui_font_OpenSansBold45.c"
    "./displays/images/ui_font_vt323_21.c"
    "./displays/images/ui_font_vt323_35.c"
    "./displays/images/ui_img_found_block_png.c"
    "./displays/images/ui_img_safe_png.c"
    "./displays/images/ui_img_eth_png.c"
    "./displays/images/ui_img_can_png.c"
    "./displays/images/ui_img_wifi_png.c"
    "./displays/images/themes/themes.c"
    "./otp/otp.cpp"
    "./otp/qrcodegen.cpp"



INCLUDE_DIRS
    "."
    "tasks"
    "http_server"
    "http_server/v2"
    "displays"
    "displays/images"
    "boards"
    "boards/drivers"
    "network"
    "../components/dns_server/include"
    "../components/bm1397/include"
    "../components/influx/include"

REQUIRES
    "app_update"
    "driver"
    "esp_adc"
    "esp_app_format"
    "esp_event"
    "esp_http_server"
    "esp_http_client"
    "esp_https_ota"
    "esp_netif"
    "esp_timer"
    "esp_wifi"
    "esp_lcd"
    "esp_psram"
    "json"
    "tcp_transport"
    "nvs_flash"
    "spiffs"
    "vfs"
    "lvgl"
    "lwip"
    "esp-tls"
    "stratum_v2"
    "libsecp256k1"
    "esp_eth"
    "coinbase_decoder"

)


set(WEB_SRC_DIR "${CMAKE_CURRENT_SOURCE_DIR}/http_server/axe-os")
include(ExternalProject)

option(BUILD_WEB "Enable web UI build" ON)

if(BUILD_WEB)
    message(STATUS "Web UI build enabled")

    if("$ENV{GITHUB_ACTIONS}" STREQUAL "true")
        spiffs_create_partition_image(www ${WEB_SRC_DIR}/dist/axe-os FLASH_IN_PROJECT)
    else()
        find_program(NPM_EXECUTABLE npm)
        if(NOT NPM_EXECUTABLE)
            message(FATAL_ERROR "npm is required when BUILD_WEB=ON. Install Node.js/npm or configure with BUILD_WEB=OFF and provide a prebuilt dist/axe-os directory.")
        endif()

        ExternalProject_Add(
            web_ui_dist
            PREFIX ${CMAKE_BINARY_DIR}/web_ui_dist
            SOURCE_DIR ${WEB_SRC_DIR}
            DOWNLOAD_COMMAND ""
            CONFIGURE_COMMAND ${NPM_EXECUTABLE} ci --no-audit --no-fund
            BUILD_COMMAND ${NPM_EXECUTABLE} run build
            INSTALL_COMMAND ""
            BUILD_ALWAYS TRUE
            BUILD_IN_SOURCE TRUE
        )

        add_dependencies(${COMPONENT_LIB} web_ui_dist)

        spiffs_create_partition_image(www ${WEB_SRC_DIR}/dist/axe-os FLASH_IN_PROJECT DEPENDS web_ui_dist)
    endif()
else()
    message(STATUS "Web UI build disabled – using prebuilt dist/ directory.")
    if(NOT EXISTS "${WEB_SRC_DIR}/dist/axe-os")
        message(FATAL_ERROR "BUILD_WEB=OFF requires a prebuilt ${WEB_SRC_DIR}/dist/axe-os directory.")
    endif()
    spiffs_create_partition_image(www ${WEB_SRC_DIR}/dist/axe-os FLASH_IN_PROJECT)
endif()
