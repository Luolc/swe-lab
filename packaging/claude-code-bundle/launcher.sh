#!/bin/sh
# The bundle entrypoint. POSIX sh, not bash — the target image may have no bash.
#
# Every line below is load-bearing; see the design doc (§5) for the full
# reasoning. The short version of each is inline, because the failure modes are
# silent and someone will otherwise "simplify" this file.

# Resolve our own directory.
#   CDPATH=  : a CDPATH in the environment makes `cd` print the resolved path to
#              stdout, which would land us somewhere else entirely.
#   --       : guards a path that begins with a dash.
#   || exit 1: without it a failed cd leaves script_dir empty and the exec below
#              silently becomes the HOST loader with HOST libs — the exact thing
#              this bundle exists to prevent, failing quietly.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1

# Claude Code extracts its built-in ripgrep and spawns it as a CHILD process.
# Children do not inherit --library-path, so that extracted rg would link
# against host glibc and break on old/musl images — while `claude --version`
# still looks perfectly healthy. Use our statically linked one instead.
USE_BUILTIN_RIPGREP=0
export USE_BUILTIN_RIPGREP

# tools/ only. NEVER the directory holding claude.real: if the raw binary were
# reachable as `claude` on PATH, anything resolving it by name would execute it
# directly, bypassing the loader below and falling back to host glibc.
PATH="${script_dir}/tools:$PATH"
export PATH

# This is a pinned artifact; silent version drift invalidates version-specific
# behavior downstream. DISABLE_AUTOUPDATER stops only the background check,
# DISABLE_UPDATES blocks every update path including manual — we need both.
DISABLE_AUTOUPDATER=1
DISABLE_UPDATES=1
export DISABLE_AUTOUPDATER DISABLE_UPDATES

# Invoke through OUR loader with OUR libraries.
#
# NEVER `export LD_LIBRARY_PATH` here. Claude Code spawns host bash, git, and
# whatever Bash(...) the prompt allows; a global LD_LIBRARY_PATH pointing at our
# old libc makes those host binaries load it and die with
# "version GLIBC_2.34 not found". The --library-path form applies to this exec
# ONLY and does not propagate to children. That failure is intermittent and
# tool-specific, so no version check will ever surface it.
exec "${script_dir}/lib/ld-linux-x86-64.so.2" \
  --library-path "${script_dir}/lib" \
  "${script_dir}/claude.real" "$@"
