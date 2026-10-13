# SpectralDenoise OFX plugin.
#
#   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
#   cmake --build build --config Release
#
# produces build/SpectralDenoise.ofx.bundle. The OpenFX SDK is downloaded at
# configure time (pinned commit) unless OPENFX_DIR points at a checkout.
cmake_minimum_required(VERSION 3.16)
project(SpectralDenoise VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(SD_BUILD_TESTS "Build the core library for the Python tests and the OFX test host" OFF)

# --- OpenFX SDK --------------------------------------------------------------
set(OPENFX_DIR "" CACHE PATH "Path to an OpenFX checkout (downloaded if empty)")
if(NOT OPENFX_DIR)
  include(FetchContent)
  FetchContent_Declare(openfx
    GIT_REPOSITORY https://github.com/AcademySoftwareFoundation/openfx.git
    GIT_TAG adb790bf0043714cae6091d58791e8cdabdd9e12)
  FetchContent_GetProperties(openfx)
  if(NOT openfx_POPULATED)
    FetchContent_Populate(openfx)
  endif()
  set(OPENFX_DIR ${openfx_SOURCE_DIR})
endif()

file(GLOB OFX_SUPPORT_SOURCES ${OPENFX_DIR}/Support/Library/*.cpp)
add_library(OfxSupport STATIC ${OFX_SUPPORT_SOURCES})
target_include_directories(OfxSupport PUBLIC ${OPENFX_DIR}/include ${OPENFX_DIR}/Support/include)
if(MSVC)
  target_compile_definitions(OfxSupport PUBLIC NOMINMAX _CRT_SECURE_NO_WARNINGS)
endif()

# --- core ----------------------------------------------------------------------
add_library(sdcore STATIC
  src/core/common.cpp
  src/core/profile.cpp
  src/core/denoise.cpp)
target_include_directories(sdcore PUBLIC src/core)
find_package(Threads REQUIRED)
target_link_libraries(sdcore PUBLIC Threads::Threads)
if(MSVC)
  target_compile_options(sdcore PRIVATE /W4 /fp:fast)
else()
  target_compile_options(sdcore PRIVATE -Wall -Wextra -Wconversion -Wno-sign-conversion -O3)
endif()

# --- plugin --------------------------------------------------------------------
find_package(OpenGL REQUIRED)
add_library(SpectralDenoise MODULE src/ofx/SpectralDenoise.cpp)
target_link_libraries(SpectralDenoise PRIVATE sdcore OfxSupport)
if(APPLE OR WIN32)
  target_link_libraries(SpectralDenoise PRIVATE OpenGL::GL)
else()
  # Linux: only the headers; the overlay looks GL up in the host at runtime.
  target_include_directories(SpectralDenoise PRIVATE ${OPENGL_INCLUDE_DIR})
  target_link_libraries(SpectralDenoise PRIVATE ${CMAKE_DL_LIBS})
endif()
set_target_properties(SpectralDenoise PROPERTIES PREFIX "" SUFFIX ".ofx")
if(NOT MSVC)
  target_compile_options(SpectralDenoise PRIVATE -Wall -Wextra)
endif()

# Bundle layout: SpectralDenoise.ofx.bundle/Contents/<arch>/SpectralDenoise.ofx
if(APPLE)
  set(SD_ARCH_DIR MacOS)
elseif(WIN32)
  set(SD_ARCH_DIR Win64)
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
  set(SD_ARCH_DIR Linux-aarch64)
else()
  set(SD_ARCH_DIR Linux-x86-64)
endif()
set(SD_BUNDLE_DIR ${CMAKE_BINARY_DIR}/SpectralDenoise.ofx.bundle)
set_target_properties(SpectralDenoise PROPERTIES
  LIBRARY_OUTPUT_DIRECTORY ${SD_BUNDLE_DIR}/Contents/${SD_ARCH_DIR}
  LIBRARY_OUTPUT_DIRECTORY_RELEASE ${SD_BUNDLE_DIR}/Contents/${SD_ARCH_DIR}
  LIBRARY_OUTPUT_DIRECTORY_RELWITHDEBINFO ${SD_BUNDLE_DIR}/Contents/${SD_ARCH_DIR}
  LIBRARY_OUTPUT_DIRECTORY_DEBUG ${SD_BUNDLE_DIR}/Contents/${SD_ARCH_DIR})

if(APPLE)
  configure_file(src/ofx/Info.plist.in ${SD_BUNDLE_DIR}/Contents/Info.plist @ONLY)
  # OpenGL is deprecated on macOS but is what OFX overlays draw with.
  target_compile_definitions(SpectralDenoise PRIVATE GL_SILENCE_DEPRECATION)
elseif(UNIX)
  # Hosts ship their own C++ runtime; don't depend on it. Export only the
  # two OFX entry points.
  target_link_options(SpectralDenoise PRIVATE -static-libstdc++ -static-libgcc
    -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/src/ofx/exports.map)
endif()

install(DIRECTORY ${SD_BUNDLE_DIR} DESTINATION . USE_SOURCE_PERMISSIONS)

# --- tests ---------------------------------------------------------------------
if(SD_BUILD_TESTS)
  add_library(sdcore_capi SHARED src/core/capi.cpp)
  target_link_libraries(sdcore_capi PRIVATE sdcore)
  set_target_properties(sdcore_capi PROPERTIES OUTPUT_NAME sdcore
    CXX_VISIBILITY_PRESET default)
  add_subdirectory(tests/host)
endif()
