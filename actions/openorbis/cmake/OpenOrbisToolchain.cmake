# Builds PS4 ELFs with the OpenOrbis SDK that actions/openorbis installs, which
# exports this file as OPENORBIS_TOOLCHAIN_FILE:
#
#   cmake -B build -DCMAKE_TOOLCHAIN_FILE="$OPENORBIS_TOOLCHAIN_FILE"
#
# Executables link against the SDK's crt1.o and SHARED libraries against its
# crtlib.o, with ld.lld itself rather than the compiler driver, whose FreeBSD
# runtime is not the SDK's. include(OpenOrbis) then turns them into eboot.bin,
# .prx libraries and .pkg installers. Reads OO_PS4_TOOLCHAIN and
# OPENORBIS_LLVM_VERSION from the cache or the environment.
set(CMAKE_SYSTEM_NAME FreeBSD)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
# A test executable would need the SDK's runtime, so CMake's compiler checks
# build a static library instead.
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

if(NOT OO_PS4_TOOLCHAIN)
  set(OO_PS4_TOOLCHAIN "$ENV{OO_PS4_TOOLCHAIN}" CACHE PATH "The OpenOrbis SDK")
endif()
if(NOT EXISTS "${OO_PS4_TOOLCHAIN}/lib/crt1.o")
  message(FATAL_ERROR "OO_PS4_TOOLCHAIN must name an OpenOrbis SDK, and "
                      "'${OO_PS4_TOOLCHAIN}' has no lib/crt1.o")
endif()
if(NOT DEFINED OPENORBIS_LLVM_VERSION)
  set(OPENORBIS_LLVM_VERSION "$ENV{OPENORBIS_LLVM_VERSION}" CACHE STRING
      "The LLVM release the SDK was built with, whose versioned tools it uses; empty for clang")
endif()
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES OO_PS4_TOOLCHAIN OPENORBIS_LLVM_VERSION)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}")

# Only the versioned names: an unversioned clang may be another release, whose
# libc++ headers don't match the SDK's.
set(_openorbis_suffix "")
if(OPENORBIS_LLVM_VERSION)
  set(_openorbis_suffix "-${OPENORBIS_LLVM_VERSION}")
endif()
find_program(OPENORBIS_CC clang${_openorbis_suffix} REQUIRED)
find_program(OPENORBIS_CXX clang++${_openorbis_suffix} REQUIRED)
find_program(OPENORBIS_LD ld.lld${_openorbis_suffix} REQUIRED)
find_program(OPENORBIS_AR llvm-ar${_openorbis_suffix} REQUIRED)
find_program(OPENORBIS_RANLIB llvm-ranlib${_openorbis_suffix} REQUIRED)
set(CMAKE_C_COMPILER "${OPENORBIS_CC}")
set(CMAKE_CXX_COMPILER "${OPENORBIS_CXX}")
set(CMAKE_LINKER "${OPENORBIS_LD}" CACHE FILEPATH "ld.lld, which links PS4 ELFs")
set(CMAKE_AR "${OPENORBIS_AR}" CACHE FILEPATH "llvm-ar")
set(CMAKE_RANLIB "${OPENORBIS_RANLIB}" CACHE FILEPATH "llvm-ranlib")

set(CMAKE_C_COMPILER_TARGET x86_64-pc-freebsd12-elf)
set(CMAKE_CXX_COMPILER_TARGET x86_64-pc-freebsd12-elf)
set(CMAKE_SYSROOT "${OO_PS4_TOOLCHAIN}")
set(CMAKE_FIND_ROOT_PATH "${OO_PS4_TOOLCHAIN}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# __ORBIS__ is what Sony's compiler defines, and the SDK's SDL looks for it.
# libc++'s headers come before the C headers they include_next.
set(CMAKE_C_FLAGS_INIT "-fPIC -funwind-tables -D__ORBIS__ -isystem \"${OO_PS4_TOOLCHAIN}/include\"")
set(CMAKE_CXX_FLAGS_INIT
    "-nostdinc++ -isystem \"${OO_PS4_TOOLCHAIN}/include/c++/v1\" ${CMAKE_C_FLAGS_INIT}")

set(_openorbis_link "-m elf_x86_64 -pie --eh-frame-hdr --script \"${OO_PS4_TOOLCHAIN}/link.x\" -L\"${OO_PS4_TOOLCHAIN}/lib\"")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_openorbis_link} \"${OO_PS4_TOOLCHAIN}/lib/crt1.o\"")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_openorbis_link} \"${OO_PS4_TOOLCHAIN}/lib/crtlib.o\"")
foreach(_openorbis_lang C CXX)
  set(CMAKE_${_openorbis_lang}_LINK_EXECUTABLE "<CMAKE_LINKER> <LINK_FLAGS> <OBJECTS> -o <TARGET> <LINK_LIBRARIES>")
  set(CMAKE_${_openorbis_lang}_CREATE_SHARED_LIBRARY "<CMAKE_LINKER> <LINK_FLAGS> <OBJECTS> -o <TARGET> <LINK_LIBRARIES>")
endforeach()
set(CMAKE_C_STANDARD_LIBRARIES_INIT "-lc -lkernel")
set(CMAKE_CXX_STANDARD_LIBRARIES_INIT "-lc++ -lc -lkernel")
# ld.lld takes no -Wl, prefix, which CMake's rpath flags carry; nothing on a
# PS4 reads an rpath anyway.
set(CMAKE_SKIP_RPATH ON CACHE BOOL "PS4 ELFs carry no rpath")
