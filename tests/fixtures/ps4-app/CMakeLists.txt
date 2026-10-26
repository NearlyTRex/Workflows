# Exercises actions/openorbis through cpp-build.yml: a C++ program and a C
# library, cross-built for the PS4 with OpenOrbisToolchain.cmake, made into
# eboot.bin and a .prx, and packaged with the files below as a fake PKG.
cmake_minimum_required(VERSION 3.21)
project(Ps4Fixture VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
include(OpenOrbis)

add_executable(fixture main.cpp)
add_library(fixture_library SHARED library.c)
set_target_properties(fixture_library PROPERTIES OUTPUT_NAME libFixture)

openorbis_add_pkg(fixture_pkg ALL
  EBOOT fixture
  LIBRARIES fixture_library
  TITLE "Workflows PS4 Fixture"
  TITLE_ID WFLO00001
  CONTENT_ID IV0000-WFLO00001_00-WORKFLOWSFIXTURE
  VERSION 01.00
  SYSTEM_VERSION 0x09000000
  ICON icon0.png
  FILES README.txt README.txt
  DIRECTORIES data data)
