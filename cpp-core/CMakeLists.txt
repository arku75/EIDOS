cmake_minimum_required(VERSION 3.16)
project(eidos_vision)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Find OpenCV via cmake config
find_package(OpenCV QUIET)

# Find Tesseract via pkg-config (no cmake config available)
find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(TESSERACT QUIET tesseract lept)
endif()

if(OpenCV_FOUND AND TESSERACT_FOUND)
    message(STATUS "OpenCV ${OpenCV_VERSION} and Tesseract found - building full vision module")

    # Vision shared library (includes C API for Python ctypes)
    add_library(eidos_vision SHARED
        ocr_engine.cpp
        vision_60fps.cpp
        eidos_vision_api.cpp
    )

    target_include_directories(eidos_vision PUBLIC
        ${OpenCV_INCLUDE_DIRS}
        ${TESSERACT_INCLUDE_DIRS}
    )

    target_link_libraries(eidos_vision
        ${OpenCV_LIBS}
        ${TESSERACT_LIBRARIES}
        pthread
    )

    # Full CLI executable
    add_executable(eidos_vision_cli
        main.cpp
    )

    target_link_libraries(eidos_vision_cli
        eidos_vision
    )

    target_compile_definitions(eidos_vision_cli PRIVATE EIDOS_FULL_BUILD)
else()
    if(NOT OpenCV_FOUND)
        message(STATUS "OpenCV not found")
    endif()
    if(NOT TESSERACT_FOUND)
        message(STATUS "Tesseract not found")
    endif()
    message(STATUS "Building stub version")

    # Stub library
    add_library(eidos_vision SHARED
        vision_stub.cpp
    )

    # Stub executable
    add_executable(eidos_vision_cli
        main_stub.cpp
    )

    target_link_libraries(eidos_vision_cli
        eidos_vision
    )
endif()

# Install
install(TARGETS eidos_vision eidos_vision_cli
    DESTINATION bin
)
