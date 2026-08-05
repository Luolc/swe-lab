#!/usr/bin/env bash
# Host-side driver: resolve the version, build hermetically, extract the tarball.
#
#   ./build.sh                     # resolve the `stable` channel, then pin it
#   ./build.sh --version 2.1.222   # pin a specific release
#   ./build.sh --pinned            # rebuild exactly what VERSION already says
#
# Design: docs/horizontal/plans/task-24-claude-code-portable-bundle.md
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"

RELEASES=${RELEASES:-https://downloads.claude.ai/claude-code-releases}
CHANNEL=${CHANNEL:-stable}
PLATFORM=${PLATFORM:-linux-x64}
DIST=${DIST:-$here/dist}

# Below this, the stream-json exit drain is capped at ~2s and can silently
# truncate the final `result` message on long runs. We consume that stream as
# our trace, so a truncated tail reads as "the agent never finished".
VERSION_FLOOR=2.1.214

# Pinned by digest: the artifact's whole point is a known glibc baseline, and
# `debian:11-slim` is a moving tag. Bumping this is a deliberate act — rerun the
# baseline assertion in Dockerfile.bundle after you do.
BASE_DIGEST=${BASE_DIGEST:-sha256:4a2e40d0d34f8f86f60ef0d79c14d3b6b3d2620825dcdf93152535b5efbaf490}

die() { printf '\nFATAL: %s\n' "$*" >&2; exit 1; }

# Sort-based semver compare: is $1 >= $2?
version_ge() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]
}

version=""
pinned=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) version=${2:-}; shift 2 || die "--version needs a value" ;;
    --version=*) version=${1#*=}; shift ;;
    --pinned) pinned=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

# ── resolve the version, and say loudly which path was taken ─────────────────
if [ -n "$version" ]; then
  echo "==> version: ${version} (EXPLICIT --version override)"
elif [ "$pinned" = 1 ]; then
  [ -f VERSION ] || die "--pinned given but no VERSION file"
  version=$(tr -d '[:space:]' < VERSION)
  echo "==> version: ${version} (PINNED, from ./VERSION — no channel lookup)"
else
  echo "==> resolving '${CHANNEL}' channel from ${RELEASES}"
  version=$(curl -fsSL --max-time 30 "${RELEASES}/${CHANNEL}" | tr -d '[:space:]') \
    || die "could not resolve channel '${CHANNEL}'"
  [ -n "$version" ] || die "channel '${CHANNEL}' returned empty"
  echo "==> version: ${version} (RESOLVED from '${CHANNEL}' channel, now pinned)"
fi

case "$version" in
  [0-9]*.[0-9]*.[0-9]*) : ;;
  *) die "resolved version '${version}' does not look like a version" ;;
esac

version_ge "$version" "$VERSION_FLOOR" \
  || die "version ${version} is below the floor ${VERSION_FLOOR}: the
       stream-json exit drain truncates the final result message below that,
       which silently invalidates our trace. Refusing to build."

echo "$version" > VERSION
echo "==> pinned ./VERSION = ${version}"

# ── build ────────────────────────────────────────────────────────────────────
command -v docker >/dev/null || die "docker not found on PATH"
mkdir -p "$DIST"

echo "==> building (base ${BASE_DIGEST})"
DOCKER_BUILDKIT=1 docker build \
  --platform linux/amd64 \
  --file Dockerfile.bundle \
  --build-arg "CLAUDE_VERSION=${version}" \
  --build-arg "PLATFORM=${PLATFORM}" \
  --build-arg "BASE_DIGEST=${BASE_DIGEST}" \
  --target export \
  --output "type=local,dest=${DIST}" \
  --progress plain \
  . || die "docker build failed"

tarball="${DIST}/claude-${version}-${PLATFORM}.tar.gz"
[ -f "$tarball" ] || die "expected ${tarball}, not produced"

echo
echo "==> built $(basename "$tarball") ($(du -h "$tarball" | cut -f1))"
sha256sum "$tarball" 2>/dev/null || shasum -a 256 "$tarball"
echo
echo "Next: ./smoke-test.sh ${tarball}"
echo
echo "REMINDER: this artifact is internal-use only. Private channels only —"
echo "          never a public release, registry, bucket or HF repo."
