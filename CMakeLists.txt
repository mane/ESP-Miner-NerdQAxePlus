# The following lines of boilerplate have to be in your project's CMakeLists
# in this exact order for cmake to work correctly
#cmake_minimum_required(VERSION 3.5)
cmake_minimum_required(VERSION 3.8)
set(CMAKE_CXX_STANDARD 17)

add_definitions(-DLV_LVGL_H_INCLUDE_SIMPLE)

# ESP-IDF normally derives PROJECT_VER from Git. Docker builds made from a Git
# worktree may not be able to resolve the worktree's external .git directory,
# so allow the wrapper/CI to provide the exact release tag explicitly.
if(DEFINED ENV{VERSION_TAG} AND NOT "$ENV{VERSION_TAG}" STREQUAL "")
    set(PROJECT_VER "$ENV{VERSION_TAG}")
    message(STATUS "Project version supplied by VERSION_TAG: ${PROJECT_VER}")
endif()

# NerdQAxe+ LTS is intentionally single-board.
# Accept an omitted BOARD for local convenience, but reject every non-NerdQAxe+
# target so CI/local builds cannot silently produce firmware for other hardware.
set(SELECTED_BOARD "NERDQAXEPLUS")
if(DEFINED ENV{BOARD} AND NOT "$ENV{BOARD}" STREQUAL "")
    set(SELECTED_BOARD "$ENV{BOARD}")
endif()

if(NOT "${SELECTED_BOARD}" STREQUAL "NERDQAXEPLUS")
    message(FATAL_ERROR "This LTS branch supports only BOARD=NERDQAXEPLUS (NerdQAxe+). Requested: ${SELECTED_BOARD}")
endif()

add_compile_definitions(NERDQAXEPLUS)
message(STATUS "Selected board: NERDQAXEPLUS (NerdQAxe+ LTS)")

set(ENV{SOURCE_DATE_EPOCH} 0)


add_compile_definitions(LV_MEM_CUSTOM=1)
add_compile_definitions(LV_MEM_CUSTOM_INCLUDE=<lvgl_psram.h>)
add_compile_definitions(LV_MEM_CUSTOM_ALLOC=lv_psram_alloc)
add_compile_definitions(LV_MEM_CUSTOM_FREE=lv_psram_free)
add_compile_definitions(LV_MEM_CUSTOM_REALLOC=lv_psram_realloc)

#add_compile_definitions(CONFIG_FREERTOS_USE_TRACE_FACILITY=1)
#add_compile_definitions(CONFIG_FREERTOS_USE_STATS_FORMATTING_FUNCTIONS=1)

# adds -I./main; needed for components to have
# access to main include files
include_directories("./main")

# this are only header files
include_directories("./components/arduinojson")

# (Not part of the boilerplate)
# This example uses an extra component for common functions such as Wi-Fi and Ethernet connection.
# set(EXTRA_COMPONENT_DIRS $ENV{IDF_PATH}/examples/common_components/protocol_examples_common)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)


project(esp-miner)
