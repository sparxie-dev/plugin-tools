cmake_minimum_required(VERSION 3.21)
project(sparxie_plugin LANGUAGES CXX)

if(NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "Sparxie plugins are x64 and built with MSVC")
endif()

# static CRT: the user's machine does not need the VC++ redistributable
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

# the build tools expect exactly one Release module.dll
add_library(module SHARED src/module.cpp)
target_include_directories(module PRIVATE third_party/sparxie-plugin-abi)
target_compile_features(module PRIVATE cxx_std_17)
target_compile_definitions(module PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN)
target_compile_options(module PRIVATE /utf-8 /W4 /WX)
set_target_properties(module PROPERTIES OUTPUT_NAME module PREFIX "")

# register native tests with add_test(); the shared workflows run ctest
enable_testing()
