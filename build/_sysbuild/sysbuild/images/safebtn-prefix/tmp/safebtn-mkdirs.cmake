# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file LICENSE.rst or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION ${CMAKE_VERSION}) # this file comes with cmake

# If CMAKE_DISABLE_SOURCE_CHANGES is set to true and the source directory is an
# existing directory in our source tree, calling file(MAKE_DIRECTORY) on it
# would cause a fatal error, even though it would be a no-op.
if(NOT EXISTS "C:/Users/basic/Videos/safebtn")
  file(MAKE_DIRECTORY "C:/Users/basic/Videos/safebtn")
endif()
file(MAKE_DIRECTORY
  "C:/Users/basic/Videos/safebtn/build/safebtn"
  "C:/Users/basic/Videos/safebtn/build/_sysbuild/sysbuild/images/safebtn-prefix"
  "C:/Users/basic/Videos/safebtn/build/_sysbuild/sysbuild/images/safebtn-prefix/tmp"
  "C:/Users/basic/Videos/safebtn/build/_sysbuild/sysbuild/images/safebtn-prefix/src/safebtn-stamp"
  "C:/Users/basic/Videos/safebtn/build/_sysbuild/sysbuild/images/safebtn-prefix/src"
  "C:/Users/basic/Videos/safebtn/build/_sysbuild/sysbuild/images/safebtn-prefix/src/safebtn-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "C:/Users/basic/Videos/safebtn/build/_sysbuild/sysbuild/images/safebtn-prefix/src/safebtn-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "C:/Users/basic/Videos/safebtn/build/_sysbuild/sysbuild/images/safebtn-prefix/src/safebtn-stamp${cfgdir}") # cfgdir has leading slash
endif()
