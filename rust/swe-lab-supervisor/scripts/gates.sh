#!/usr/bin/env bash
# The crate's quality gates, in the order CI runs them. This is the one list:
# CI (`.github/workflows/ci.yml`, job `rust`) runs this file directly, and
# `scripts/check-in-container.sh` runs it inside the pinned Rust image for a
# machine with no local toolchain. Run from anywhere; it cds to the crate.
set -euo pipefail
cd "$(dirname "$0")/.."

target=x86_64-unknown-linux-musl

echo "==> fmt"
cargo fmt --all -- --check

echo "==> clippy (warnings are errors)"
cargo clippy --all-targets --all-features -- -D warnings

echo "==> test"
cargo test

echo "==> doc (broken links are errors)"
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps

# The release artifact links self-contained only while every dependency is
# pure Rust; a crate that compiles or links C would need a musl C toolchain
# on every builder. Caught here by name, before the build gets to try.
echo "==> no C in the dependency tree"
if cargo tree -e normal,build --prefix none --format '{p}' \
    | grep -E '^(cc|cmake|pkg-config) |-sys '; then
  echo "a dependency above compiles or links C; the build must stay pure Rust" >&2
  exit 1
fi

echo "==> static release build"
cargo build --release --target "$target"

# "Static" is a structural fact — no PT_INTERP program header, so no dynamic
# linker is needed — not a word in `file` or `ldd` output, whose phrasing moves
# with libc and version (a modern musl build prints `static-pie linked`).
bin="target/$target/release/swe-lab-supervisor"
echo "==> $bin needs no dynamic linker"
if readelf -l "$bin" | grep -q INTERP; then
  echo "$bin has a PT_INTERP header: it is not static" >&2
  readelf -l "$bin" >&2
  exit 1
fi
"$bin" --version
"$bin" criteria
