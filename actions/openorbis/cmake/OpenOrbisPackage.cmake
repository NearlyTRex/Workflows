# Builds one openorbis_add_pkg package; run with cmake -P from the script it
# generates, which sets PKGTOOL, STAGE, OUTPUT_DIRECTORY, PKG, FILES and
# DIRECTORIES (<source> <path in package> pairs), and the param.sfo metadata.
# Stages the package's files afresh, writes param.sfo with PkgTool and the gp4
# project naming every file and directory, builds PKG from them, and validates
# it.
cmake_minimum_required(VERSION 3.21)

# PkgTool.Core is .NET, which otherwise needs ICU, missing from slim images.
set(ENV{DOTNET_SYSTEM_GLOBALIZATION_INVARIANT} 1)

function(run)
  execute_process(COMMAND ${ARGN} WORKING_DIRECTORY "${STAGE}" COMMAND_ERROR_IS_FATAL ANY)
endfunction()

# The <dir> elements for the directories in dirs under parent, nested.
function(gp4_dirs parent indent out)
  set(xml "")
  foreach(dir IN LISTS dirs)
    get_filename_component(dir_parent "${dir}" DIRECTORY)
    if(dir_parent STREQUAL parent)
      get_filename_component(name "${dir}" NAME)
      gp4_dirs("${dir}" "${indent}\t" children)
      if(children)
        string(APPEND xml "${indent}<dir targ_name=\"${name}\">\n${children}${indent}</dir>\n")
      else()
        string(APPEND xml "${indent}<dir targ_name=\"${name}\" />\n")
      endif()
    endif()
  endforeach()
  set(${out} "${xml}" PARENT_SCOPE)
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

# The project create-gp4 writes, but with the directories the files are in:
# create-gp4's are fixed to the SDK samples' layout, and pkg_build fails on a
# file outside them. It sits in the stage, since its paths are relative to it,
# and isn't one of the files. openorbis_add_pkg keeps &, <, " and spaces out of
# the paths.
set(dirs "")
set(files_xml "")
foreach(path IN LISTS paths)
  string(APPEND files_xml "\t\t<file targ_path=\"${path}\" orig_path=\"${path}\" />\n")
  get_filename_component(dir "${path}" DIRECTORY)
  while(dir)
    list(APPEND dirs "${dir}")
    get_filename_component(dir "${dir}" DIRECTORY)
  endwhile()
endforeach()
list(REMOVE_DUPLICATES dirs)
list(SORT dirs)
gp4_dirs("" "\t\t" dirs_xml)
string(TIMESTAMP timestamp "%Y-%m-%d %H:%M:%S")
file(WRITE "${STAGE}/pkg.gp4" "<?xml version=\"1.0\"?>
<psproject fmt=\"gp4\" version=\"1000\">
\t<volume>
\t\t<volume_type>pkg_ps4_app</volume_type>
\t\t<volume_id>PS4VOLUME</volume_id>
\t\t<volume_ts>${timestamp}</volume_ts>
\t\t<package content_id=\"${CONTENT_ID}\" passcode=\"00000000000000000000000000000000\" storage_type=\"digital50\" app_type=\"full\" />
\t\t<chunk_info chunk_count=\"1\" scenario_count=\"1\">
\t\t\t<chunks>
\t\t\t\t<chunk id=\"0\" layer_no=\"0\" label=\"Chunk #0\" />
\t\t\t</chunks>
\t\t\t<scenarios default_id=\"0\">
\t\t\t\t<scenario id=\"0\" type=\"sp\" initial_chunk_count=\"1\" label=\"Scenario #0\">0</scenario>
\t\t\t</scenarios>
\t\t</chunk_info>
\t</volume>
\t<files img_no=\"0\">
${files_xml}\t</files>
\t<rootdir>
${dirs_xml}\t</rootdir>
</psproject>
")
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
