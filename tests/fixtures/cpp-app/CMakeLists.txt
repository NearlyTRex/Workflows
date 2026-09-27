# Exercises cpp-build.yml: a library, a program and a test, built with CMake on
# Linux (gcc) and Windows (MSVC, set up by the workflow).
cmake_minimum_required(VERSION 3.20)
project(Fixture VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(fixture fixture.cpp)
add_executable(fixture_test fixture_test.cpp)
target_link_libraries(fixture_test PRIVATE fixture)

enable_testing()
add_test(NAME fixture_test COMMAND fixture_test)
