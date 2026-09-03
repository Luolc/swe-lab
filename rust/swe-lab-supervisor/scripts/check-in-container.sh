#!/usr/bin/env bash
# Run the crate's gates (scripts/gates.sh), or any cargo command, inside the
# official Rust image pinned to the toolchain in rust-toolchain.toml — for a
# machine that has Docker but no local toolchain, and because the deliverable
# is a static binary for arbitrary images, so a pinned container is closer to
# the delivery shape than any workstation.
#
#   scripts/check-in-container.sh                 # the gates, as CI runs them
#   scripts/check-in-container.sh cargo test foo  # one command instead
#
# The image adds the musl target and the lint components on top of the
# official one, so the container runs as the invoking user and never writes
# into the toolchain. Those two lines are a cache, not a second pin: rustup
# installs whatever rust-toolchain.toml lists when cargo first runs in the
# crate, so a target added there but not here costs a download per run, not
# a mismatch. Cargo's registry and the build output are kept under
# target/container/ on the host, so a second run does not download or rebuild
# the world. Containers are always `--rm`; nothing is left running.
set -euo pipefail
crate=$(cd "$(dirname "$0")/.." && pwd)
# The whole repository is mounted, not just the crate: the criterion text is
# `include_str!`-ed from the Python side's artifact file two levels up.
repo=$(cd "$crate/../.." && pwd)
crate_in_repo=${crate#"$repo"/}
version=$(sed -n 's/^channel = "\(.*\)"$/\1/p' "$crate/rust-toolchain.toml")
[ -n "$version" ] || { echo "no channel in rust-toolchain.toml" >&2; exit 1; }
image="swe-lab-rust-build:$version"

docker build --quiet --tag "$image" - <<DOCKERFILE >/dev/null
FROM rust:$version
RUN rustup target add x86_64-unknown-linux-musl \
 && rustup component add rustfmt clippy
DOCKERFILE

cache="$crate/target/container"
mkdir -p "$cache/cargo" "$cache/target"
if [ $# -eq 0 ]; then
  set -- scripts/gates.sh
fi
# --init: an init as PID 1 reaps the orphans the tests make on purpose;
# `cargo` as PID 1 would not, and a killed grandchild would stay a zombie
# that still counts as a member of its process group.
exec docker run --rm --init \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp \
  --env CARGO_HOME=/cargo \
  --volume "$repo:/repo" \
  --volume "$cache/cargo:/cargo" \
  --volume "$cache/target:/repo/$crate_in_repo/target" \
  --workdir "/repo/$crate_in_repo" \
  "$image" "$@"
