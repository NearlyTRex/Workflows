# Builds one openorbis_add_pkg package; run with cmake -P from the script it
# generates, which sets PKGTOOL, CREATE_GP4, STAGE, OUTPUT_DIRECTORY, PKG,
# FILES and DIRECTORIES (<source> <path in package> pairs), and the param.sfo
# metadata. Stages the package's files afresh, writes param.sfo and the gp4
# project with the SDK's tools, builds PKG from them, and validates it.
cmake_minimum_required(VERSION 3.21)

# PkgTool.Core is .NET, which otherwise needs ICU, missing from slim images.
set(ENV{DOTNET_SYSTEM_GLOBALIZATION_INVARIANT} 1)

function(run)
  execute_process(COMMAND ${ARGN} WORKING_DIRECTORY "${STAGE}" COMMAND_ERROR_IS_FATAL ANY)
endfunction()

file(REMOVE_RECURSE "${STAGE}")
set(paths "")
while(FILES)
  list(POP_FRONT FILES source path)
  get_filename_component(parent "${STAGE}/${path}" DIRECTORY)
  file(MAKE_DIRECTORY "${parent}")
  file(COPY_FILE "${source}" "${STAGE}/${path}" ONLY_IF_DIFFERENT)
  list(APPEND paths "${path}")
endwhile()
while(DIRECTORIES)
  list(POP_FRONT DIRECTORIES source path)
  file(GLOB_RECURSE contents RELATIVE "${source}" "${source}/*")
  foreach(file IN LISTS contents)
    get_filename_component(parent "${STAGE}/${path}/${file}" DIRECTORY)
    file(MAKE_DIRECTORY "${parent}")
    file(COPY_FILE "${source}/${file}" "${STAGE}/${path}/${file}")
    list(APPEND paths "${path}/${file}")
  endforeach()
endwhile()

# The entries and sizes the SDK's samples write.
run("${PKGTOOL}" sfo_new sce_sys/param.sfo)
foreach(entry "APP_TYPE;Integer;4;1" "APP_VER;Utf8;8;${VERSION}" "ATTRIBUTE;Integer;4;${ATTRIBUTE}"
              "CATEGORY;Utf8;4;${CATEGORY}" "CONTENT_ID;Utf8;48;${CONTENT_ID}"
              "DOWNLOAD_DATA_SIZE;Integer;4;0" "SYSTEM_VER;Integer;4;${SYSTEM_VERSION}"
              "TITLE;Utf8;128;${TITLE}" "TITLE_ID;Utf8;12;${TITLE_ID}" "VERSION;Utf8;8;${VERSION}")
  list(POP_FRONT entry key type size)
  run("${PKGTOOL}" sfo_setentry sce_sys/param.sfo ${key} --type ${type} --maxsize ${size} --value "${entry}")
endforeach()
list(APPEND paths sce_sys/param.sfo)

# create-gp4 takes the paths as one space-separated argument, which is why
# openorbis_add_pkg refuses package paths with spaces. The project sits in the
# stage, since its paths are relative to it, and isn't one of the files.
list(JOIN paths " " joined)
run("${CREATE_GP4}" -out pkg.gp4 "--content-id=${CONTENT_ID}" --files "${joined}")
file(MAKE_DIRECTORY "${OUTPUT_DIRECTORY}")
file(REMOVE "${PKG}")
run("${PKGTOOL}" pkg_build pkg.gp4 "${OUTPUT_DIRECTORY}")

# A fake PKG starts with the CNT magic, checked rather than trusting the exit status.
if(NOT EXISTS "${PKG}")
  message(FATAL_ERROR "PkgTool wrote no ${PKG}")
endif()
file(READ "${PKG}" magic LIMIT 4 HEX)
if(NOT magic STREQUAL "7f434e54")
  message(FATAL_ERROR "${PKG} doesn't start with a PKG header")
endif()
execute_process(COMMAND "${PKGTOOL}" pkg_validate "${PKG}" OUTPUT_VARIABLE validation
                ERROR_VARIABLE validation COMMAND_ERROR_IS_FATAL ANY)
if(NOT validation MATCHES "\\[OK\\]")
  message(FATAL_ERROR "PkgTool's pkg_validate passed no check of ${PKG}:\n${validation}")
endif()
message(STATUS "${PKG}")
