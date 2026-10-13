# Scriptable OFX test host (tests only), built on OpenFX HostSupport.
find_package(EXPAT REQUIRED)
file(GLOB OFX_HOST_SOURCES ${OPENFX_DIR}/HostSupport/src/*.cpp)
add_library(OfxHostSupport STATIC ${OFX_HOST_SOURCES})
target_include_directories(OfxHostSupport PUBLIC ${OPENFX_DIR}/include ${OPENFX_DIR}/HostSupport/include)
target_link_libraries(OfxHostSupport PUBLIC EXPAT::EXPAT ${CMAKE_DL_LIBS})
set_target_properties(OfxHostSupport PROPERTIES CXX_VISIBILITY_PRESET default)

add_executable(sd_ofx_host sd_ofx_host.cpp)
target_link_libraries(sd_ofx_host PRIVATE OfxHostSupport)
set_target_properties(sd_ofx_host PROPERTIES CXX_VISIBILITY_PRESET default)
