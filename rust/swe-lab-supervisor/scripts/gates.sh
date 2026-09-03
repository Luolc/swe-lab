#!/usr/bin/env bash
# The crate's quality gates, in the order CI runs them. This is the one list:
# CI (`.github/workflows/ci.yml`, job `rust`) runs this file directly, and
# `scripts/check-in-container.sh` runs it inside the pinned Rust image for a
# machine with no local toolchain. Run from anywhere; it cds to the crate.
#
# Two of the gates below conclude from an *absence* — no C crate in the tree,
# no PT_INTERP header in the binary. "grep finds no X" means something only
# once the input is proven to be the right thing: a failed `cargo tree` or a
# missing artifact also contain no X. So each of those asserts its positive
# premise first — the command succeeded, the output is what it claims to be —
# and only then the absence.
set -euo pipefail
cd "$(dirname "$0")/.."

crate=swe-lab-supervisor
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
# on every builder. Caught here by name, before the build gets to try — and
# for the graph that ships: `cargo tree` resolves the host's graph unless
# told the target, so a dependency behind a musl-only `[target.…]` section
# would be linked into the artifact and absent from the host's tree.
echo "==> no C in the dependency tree ($target)"
tree=$(cargo tree --target "$target" -e normal,build --prefix none --format '{p}')
printf '%s\n' "$tree" | grep -q "^$crate " \
  || { echo "cargo tree did not list $crate itself; its output is not a dependency tree" >&2; exit 1; }
if printf '%s\n' "$tree" | grep -E '^(cc|cmake|pkg-config) |-sys '; then
  echo "a dependency above compiles or links C; the build must stay pure Rust" >&2
  exit 1
fi

echo "==> static release build"
cargo build --release --target "$target"

# "Static" is a structural fact — no PT_INTERP program header, so no dynamic
# linker is needed — not a word in `file` or `ldd` output, whose phrasing moves
# with libc and version (a modern musl build prints `static-pie linked`).
bin="target/$target/release/$crate"
echo "==> $bin needs no dynamic linker"
[ -x "$bin" ] || { echo "no artifact at $bin" >&2; exit 1; }
headers=$(readelf -l "$bin") || { echo "$bin is not an ELF file" >&2; exit 1; }
printf '%s\n' "$headers" | grep -q 'Program Headers' \
  || { echo "$bin has no program headers; readelf output is not a header table" >&2; exit 1; }
if printf '%s\n' "$headers" | grep -q INTERP; then
  echo "$bin has a PT_INTERP header: it is not static" >&2
  printf '%s\n' "$headers" >&2
  exit 1
fi
"$bin" --version
"$bin" criteria
