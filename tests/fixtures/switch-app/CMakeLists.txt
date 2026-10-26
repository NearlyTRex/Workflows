# Exercises cpp-build.yml's devkitpro input: a C++ program built with
# devkitPro's own Switch.cmake in its pinned devkitA64 image, and made into an
# NRO, with a NACP and a romfs, by devkitPro's own nx_ helpers.
cmake_minimum_required(VERSION 3.13)
project(SwitchFixture VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(fixture main.cpp)
nx_generate_nacp(fixture.nacp NAME "Workflows Switch Fixture" AUTHOR "Workflows" VERSION 1.0.0)
nx_create_nro(fixture NACP fixture.nacp ROMFS romfs)
