# Exercises cpp-build.yml's msvc-uwp and ci-msix: a C++/WinRT CoreWindow app,
# built for UWP with MSVC in vcvarsall's uwp mode, and packaged by ci-msix as a
# signed MSIX for Xbox Dev Mode, with the VC++ runtime bundled and its imports
# checked.
cmake_minimum_required(VERSION 3.21)
project(UwpFixture VERSION 1.0.0 LANGUAGES CXX)

# C++/WinRT uses the standard coroutines from C++20 on; before, it needs MSVC's
# experimental ones, which current MSVC refuses (STL1011).
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# A UWP app links WindowsApp.lib, not the desktop libraries CMake adds by default.
set(CMAKE_CXX_STANDARD_LIBRARIES "WindowsApp.lib")

add_executable(fixture WIN32 app.cpp)
target_compile_definitions(fixture PRIVATE WINAPI_FAMILY=WINAPI_FAMILY_APP UNICODE _UNICODE)
target_compile_options(fixture PRIVATE /permissive-)
target_link_options(fixture PRIVATE /APPCONTAINER)
