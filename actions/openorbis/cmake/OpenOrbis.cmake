# What a PS4 runs, from targets built with OpenOrbisToolchain.cmake, made by the
# SDK's own tools (create-fself and PkgTool.Core):
#
#   openorbis_add_fself(<target> [PAID <id>])
#     eboot.bin from an executable, or <name>.prx from a SHARED library, in
#     <binary dir>/<target>-ps4[/<config>]. Built with the target; the path is
#     its OPENORBIS_FSELF property.
#
#   openorbis_add_pkg(<name> EBOOT <target> TITLE <title> TITLE_ID <id> CONTENT_ID <id> ICON <png>
#                     [VERSION 01.00] [CATEGORY gd] [ATTRIBUTE 0] [SYSTEM_VERSION 0]
#                     [LIBRARIES <target>...] [FILES <source> <path>...]
#                     [DIRECTORIES <source dir> <path>...] [OUTPUT_DIRECTORY <dir>] [ALL])
#     A custom target building <OUTPUT_DIRECTORY>/<CONTENT_ID>.pkg, a fake PKG
#     to install with a homebrew package installer. It holds the EBOOT target's
#     eboot.bin, a param.sfo of the metadata, the 512x512 ICON as icon0.png, the
#     SDK's startup modules, each LIBRARIES target's .prx in sce_module/, and
#     FILES and DIRECTORIES at the package paths they are paired with. ALL builds
#     it with the default target.
include_guard(GLOBAL)

if(CMAKE_VERSION VERSION_LESS 3.21)
  message(FATAL_ERROR "OpenOrbis.cmake needs CMake 3.21 or later")
endif()
if(NOT EXISTS "${OO_PS4_TOOLCHAIN}/lib/crt1.o")
  message(FATAL_ERROR "OpenOrbis.cmake needs OO_PS4_TOOLCHAIN, set by OpenOrbisToolchain.cmake")
endif()

# The SDK's tools for this host.
if(CMAKE_HOST_APPLE)
  set(_openorbis_bin "${OO_PS4_TOOLCHAIN}/bin/macos")
  set(_openorbis_fself create-fself-macos)
else()
  set(_openorbis_bin "${OO_PS4_TOOLCHAIN}/bin/linux")
  set(_openorbis_fself create-fself)
endif()
find_program(OPENORBIS_CREATE_FSELF ${_openorbis_fself} PATHS "${_openorbis_bin}" NO_DEFAULT_PATH REQUIRED)
# actions/openorbis's PkgTool.Core, OPENORBIS_BIN's, runs the SDK's with the
# OpenSSL 1.1 it needs.
find_program(OPENORBIS_PKGTOOL PkgTool.Core PATHS "$ENV{OPENORBIS_BIN}" "${_openorbis_bin}" NO_DEFAULT_PATH REQUIRED)
set(_openorbis_package_script "${CMAKE_CURRENT_LIST_DIR}/OpenOrbisPackage.cmake")

# Where a target's PS4 files go: per configuration under a multi-config generator.
function(_openorbis_output_dir target out)
  set(dir "${CMAKE_CURRENT_BINARY_DIR}/${target}-ps4")
  get_property(multi GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
  if(multi)
    string(APPEND dir "/$<CONFIG>")
  endif()
  set(${out} "${dir}" PARENT_SCOPE)
endfunction()

function(openorbis_add_fself target)
  cmake_parse_arguments(PARSE_ARGV 1 arg "" "PAID" "")
  if(NOT arg_PAID)
    # The program authority ID the SDK's samples use.
    set(arg_PAID 0x3800000000000011)
  endif()
  _openorbis_output_dir(${target} dir)
  get_target_property(type ${target} TYPE)
  if(type STREQUAL "EXECUTABLE")
    set(out "${dir}/eboot.bin")
    set(kind --eboot "${out}")
  elseif(type STREQUAL "SHARED_LIBRARY")
    get_target_property(name ${target} OUTPUT_NAME)
    if(NOT name)
      set(name ${target})
    endif()
    set(out "${dir}/${name}.prx")
    set(kind "--lib=${out}")
  else()
    message(FATAL_ERROR "openorbis_add_fself: ${target} is a ${type}, not an executable or SHARED library")
  endif()
  add_custom_command(OUTPUT "${out}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${dir}"
    COMMAND "${CMAKE_COMMAND}" -E env "OO_PS4_TOOLCHAIN=${OO_PS4_TOOLCHAIN}"
            "${OPENORBIS_CREATE_FSELF}" "-in=$<TARGET_FILE:${target}>" "-out=${dir}/${target}.oelf"
            ${kind} --paid ${arg_PAID}
    DEPENDS ${target}
    COMMENT "Creating ${out}"
    VERBATIM)
  add_custom_target(${target}_fself ALL DEPENDS "${out}")
  set_target_properties(${target} PROPERTIES OPENORBIS_FSELF "${out}")
endfunction()

# Fails the configure, naming the package, unless value matches pattern, which
# is described as what it must be. CMake's regexes have no {n}.
function(_openorbis_check name field value pattern description)
  if(NOT value MATCHES "${pattern}")
    message(FATAL_ERROR "openorbis_add_pkg(${name}): ${field} '${value}' must be ${description}")
  endif()
endfunction()

# Appends each <source> <path in package> pair in pairs, checked, to the list out.
function(_openorbis_pairs name field pairs out)
  list(LENGTH pairs count)
  math(EXPR odd "${count} % 2")
  if(odd)
    message(FATAL_ERROR "openorbis_add_pkg(${name}): ${field} takes <source> <path in package> pairs")
  endif()
  set(result ${${out}})
  while(pairs)
    list(POP_FRONT pairs source path)
    _openorbis_check(${name} "${field} path" "${path}" "^[^ \t/&<\"][^ \t&<\"]*$"
                     "relative, without spaces, &, < or \", as the gp4 project holds it")
    cmake_path(ABSOLUTE_PATH source)
    list(APPEND result "${source}" "${path}")
  endwhile()
  set(${out} ${result} PARENT_SCOPE)
endfunction()

function(openorbis_add_pkg name)
  cmake_parse_arguments(PARSE_ARGV 1 arg "ALL"
    "EBOOT;TITLE;TITLE_ID;CONTENT_ID;ICON;VERSION;CATEGORY;ATTRIBUTE;SYSTEM_VERSION;OUTPUT_DIRECTORY"
    "LIBRARIES;FILES;DIRECTORIES")
  foreach(required EBOOT TITLE TITLE_ID CONTENT_ID ICON)
    if(NOT arg_${required})
      message(FATAL_ERROR "openorbis_add_pkg(${name}): ${required} is required")
    endif()
  endforeach()
  foreach(default "VERSION;01.00" "CATEGORY;gd" "ATTRIBUTE;0" "SYSTEM_VERSION;0"
                  "OUTPUT_DIRECTORY;${CMAKE_CURRENT_BINARY_DIR}")
    list(POP_FRONT default field)
    if(NOT DEFINED arg_${field})
      set(arg_${field} "${default}")
    endif()
  endforeach()
  _openorbis_check(${name} TITLE_ID "${arg_TITLE_ID}" "^[A-Z][A-Z][A-Z][A-Z][0-9][0-9][0-9][0-9][0-9]$"
                   "four capital letters and five digits, e.g. BREW00083")
  string(LENGTH "${arg_CONTENT_ID}" length)
  if(NOT length EQUAL 36)
    set(arg_CONTENT_ID "${arg_CONTENT_ID} (${length} characters)")
  endif()
  _openorbis_check(${name} CONTENT_ID "${arg_CONTENT_ID}" "^[A-Z][A-Z][0-9][0-9][0-9][0-9]-${arg_TITLE_ID}_00-[A-Z0-9]+$"
                   "36 characters, the TITLE_ID in a label, e.g. IV0000-${arg_TITLE_ID}_00-HELLOWORLD000000")
  _openorbis_check(${name} VERSION "${arg_VERSION}" "^[0-9][0-9]\\.[0-9][0-9]$" "two digits, a dot and two digits")
  _openorbis_check(${name} CATEGORY "${arg_CATEGORY}" "^[a-z]+$" "lower case, e.g. gd for an application")
  string(LENGTH "${arg_TITLE}" length)
  _openorbis_check(${name} TITLE "${arg_TITLE}" "^[^;]+$" "without semicolons, which split CMake lists")
  if(length GREATER 127)
    message(FATAL_ERROR "openorbis_add_pkg(${name}): TITLE is longer than param.sfo's 127 bytes")
  endif()
  # param.sfo takes these as integers; written in hex, as system versions are.
  math(EXPR attribute "${arg_ATTRIBUTE}")
  math(EXPR system_version "${arg_SYSTEM_VERSION}")

  get_target_property(eboot ${arg_EBOOT} OPENORBIS_FSELF)
  if(NOT eboot)
    openorbis_add_fself(${arg_EBOOT})
    get_target_property(eboot ${arg_EBOOT} OPENORBIS_FSELF)
  endif()
  cmake_path(ABSOLUTE_PATH arg_ICON)
  set(files "${eboot}" eboot.bin "${arg_ICON}" sce_sys/icon0.png
            "${OO_PS4_TOOLCHAIN}/src/modules/right.sprx" sce_sys/about/right.sprx
            "${OO_PS4_TOOLCHAIN}/src/modules/libc.prx" sce_module/libc.prx
            "${OO_PS4_TOOLCHAIN}/src/modules/libSceFios2.prx" sce_module/libSceFios2.prx)
  set(depends ${arg_EBOOT}_fself)
  foreach(library IN LISTS arg_LIBRARIES)
    get_target_property(prx ${library} OPENORBIS_FSELF)
    if(NOT prx)
      openorbis_add_fself(${library})
      get_target_property(prx ${library} OPENORBIS_FSELF)
    endif()
    cmake_path(GET prx FILENAME prx_name)
    list(APPEND files "${prx}" "sce_module/${prx_name}")
    list(APPEND depends ${library}_fself)
  endforeach()
  _openorbis_pairs(${name} FILES "${arg_FILES}" files)
  set(directories "")
  _openorbis_pairs(${name} DIRECTORIES "${arg_DIRECTORIES}" directories)
  # The build depends on what the files and directories hold, not the pairs'
  # package paths, which are relative.
  set(sources ${files})
  list(FILTER sources INCLUDE REGEX "^/")
  set(directory_sources ${directories})
  list(FILTER directory_sources INCLUDE REGEX "^/")
  foreach(directory IN LISTS directory_sources)
    file(GLOB_RECURSE contents CONFIGURE_DEPENDS "${directory}/*")
    list(APPEND sources ${contents})
  endforeach()
  _openorbis_output_dir(${name} work)
  cmake_path(ABSOLUTE_PATH arg_OUTPUT_DIRECTORY)
  set(pkg "${arg_OUTPUT_DIRECTORY}/${arg_CONTENT_ID}.pkg")
  # The script runs at build time, when the configuration in these paths is known.
  string(CONCAT script
    "set(PKGTOOL [==[${OPENORBIS_PKGTOOL}]==])\n"
    "set(STAGE [==[${work}/stage]==])\n"
    "set(OUTPUT_DIRECTORY [==[${arg_OUTPUT_DIRECTORY}]==])\n"
    "set(PKG [==[${pkg}]==])\n"
    "set(FILES [==[${files}]==])\n"
    "set(DIRECTORIES [==[${directories}]==])\n"
    "set(CONTENT_ID [==[${arg_CONTENT_ID}]==])\n"
    "set(TITLE [==[${arg_TITLE}]==])\n"
    "set(TITLE_ID [==[${arg_TITLE_ID}]==])\n"
    "set(VERSION [==[${arg_VERSION}]==])\n"
    "set(CATEGORY [==[${arg_CATEGORY}]==])\n"
    "set(ATTRIBUTE ${attribute})\n"
    "set(SYSTEM_VERSION ${system_version})\n"
    "include([==[${_openorbis_package_script}]==])\n")
  file(GENERATE OUTPUT "${work}/package.cmake" CONTENT "${script}")
  add_custom_command(OUTPUT "${pkg}"
    COMMAND "${CMAKE_COMMAND}" -P "${work}/package.cmake"
    DEPENDS ${depends} ${sources} "${work}/package.cmake" "${_openorbis_package_script}"
    COMMENT "Packaging ${pkg}"
    VERBATIM)
  set(all "")
  if(arg_ALL)
    set(all ALL)
  endif()
  add_custom_target(${name} ${all} DEPENDS "${pkg}")
endfunction()
