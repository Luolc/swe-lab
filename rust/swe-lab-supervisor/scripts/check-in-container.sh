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
# The image is the official one for the file's `channel`, with the file
# itself copied in and `rustup toolchain install` run on it, so the image
# holds exactly the components and targets the file lists — nothing here
# names one — and it is tagged by the file's digest, so a changed file is a
# new image. The container runs as the invoking user, so nothing it writes
# lands in the image; and rustup is told not to install at run time
# (`RUSTUP_AUTO_INSTALL=0`): the official image leaves the toolchain
# world-writable, and rustup would otherwise put a missing item into the
# throwaway container and go on, green — so an image missing what the file
# lists fails its gates instead, and a green gate vouches for the image.
# Cargo's registry and the build output are kept under target/container/ on
# the host, so a second run does not download or rebuild the world.
# Containers are always `--rm`; nothing is left running.
set -euo pipefail
crate=$(cd "$(dirname "$0")/.." && pwd)
# The whole repository is mounted, not just the crate: the criterion text is
# `include_str!`-ed from the Python side's artifact file two levels up.
repo=$(cd "$crate/../.." && pwd)
crate_in_repo=${crate#"$repo"/}
version=$(sed -n 's/^channel = "\(.*\)"$/\1/p' "$crate/rust-toolchain.toml")
[ -n "$version" ] || { echo "no channel in rust-toolchain.toml" >&2; exit 1; }
pin=$(sha256sum "$crate/rust-toolchain.toml" | cut -c1-12)
image="swe-lab-rust-build:$version-$pin"

docker build --quiet --tag "$image" - <<DOCKERFILE >/dev/null
FROM rust:$version
# --no-self-update: rustup itself stays what the base image ships, so two
# builds of one tag run the same rustup.
RUN mkdir /pin \
 && echo '$(base64 -w0 "$crate/rust-toolchain.toml")' | base64 -d > /pin/rust-toolchain.toml \
 && cd /pin && rustup toolchain install --no-self-update
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
  --env RUSTUP_AUTO_INSTALL=0 \
  --volume "$repo:/repo" \
  --volume "$cache/cargo:/cargo" \
  --volume "$cache/target:/repo/$crate_in_repo/target" \
  --workdir "/repo/$crate_in_repo" \
  "$image" "$@"
