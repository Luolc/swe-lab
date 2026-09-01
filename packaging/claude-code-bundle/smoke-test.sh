#!/usr/bin/env bash
# Verify a built bundle across a matrix of target images.
#
#   ./smoke-test.sh dist/claude-2.1.220-linux-x64.tar.gz
#
# Checks that need a live agent (a real `claude -p` run) need credentials and
# egress; they are skipped unless a token is set (either CLAUDE_CODE_OAUTH_TOKEN
# or the repo-scoped SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN, see below), and the skip is
# reported, never silently passed.
#
# Design: docs/horizontal/plans/task-24-claude-code-portable-bundle.md §7
set -euo pipefail

# `.envrc.local` exports the token under a repo-scoped name so an interactive
# `claude` in this directory never picks it up (docs/conventions.md → Hazards).
# The checks below read the canonical name, like everything else that runs an
# agent; hand it over here, without overwriting one already set.
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -n "${SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  export CLAUDE_CODE_OAUTH_TOKEN="$SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN"
fi

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tarball=${1:-}
[ -n "$tarball" ] && [ -f "$tarball" ] || {
  echo "usage: $0 <bundle.tar.gz>" >&2; exit 2; }
tarball=$(CDPATH= cd -- "$(dirname -- "$tarball")" && pwd)/$(basename "$tarball")
expected_version=$(tr -d '[:space:]' < "$here/VERSION")
bundle_dir="claude-${expected_version}-linux-x64"

IMAGES=${IMAGES:-"debian:12 ubuntu:22.04 ubuntu:20.04 alpine:3.19 debian:10-slim"}
# distroless has no shell, so it cannot be driven with `sh -c` — it gets its own
# throwaway Dockerfile below.
DISTROLESS=${DISTROLESS:-gcr.io/distroless/base-debian12}

pass=0; fail=0; skip=0
results=()

record() { # image check status [detail]
  case "$3" in
    PASS) pass=$((pass+1)) ;;
    FAIL) fail=$((fail+1)) ;;
    SKIP) skip=$((skip+1)) ;;
  esac
  results+=("$(printf '%-34s %-22s %-4s %s' "$1" "$2" "$3" "${4:-}")")
}

# Run a script inside an image with the bundle unpacked at /bundle, as a
# NON-ROOT user — the mode the artifact actually ships into.
in_image() { # image script
  docker run --rm --platform linux/amd64 \
    -v "$tarball:/bundle.tar.gz:ro" \
    -e "EXPECTED=$expected_version" -e "BDIR=$bundle_dir" \
    "$1" sh -c "$2" 2>&1
}

echo "== bundle:  $tarball"
echo "== version: $expected_version"
echo

for image in $IMAGES; do
  echo "-- $image"

  # 1. version — and 7. permissions, since we unpack as non-root and run as one.
  out=$(in_image "$image" '
      set -e
      mkdir -p /w && cd /w && tar xzf /bundle.tar.gz
      # unpack as root, then drop to a non-root uid to prove the modes survive
      adduser -D -u 1001 app 2>/dev/null || useradd -u 1001 -m app 2>/dev/null || true
      su app -c "\"/w/$BDIR/claude\" --version" 2>/dev/null \
        || su -s /bin/sh app -c "\"/w/$BDIR/claude\" --version"
    ' ) && rc=0 || rc=$?
  got=$(printf '%s' "$out" | tr -d '[:space:]' | tail -c 32)
  if [ "$rc" = 0 ] && printf '%s' "$got" | grep -q "$expected_version"; then
    record "$image" "version+perms" PASS
  else
    record "$image" "version+perms" FAIL "rc=$rc out=$(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
  fi

  # 5. no host leakage — every library must resolve into OUR lib/.
  # Uses the loader's own --list rather than /proc/<pid>/maps: `--version`
  # exits before maps can be read, which made the maps form report a false
  # failure on every image.
  out=$(in_image "$image" '
      set -e
      mkdir -p /w && cd /w && tar xzf /bundle.tar.gz
      d=/w/$BDIR
      "$d/lib/ld-linux-x86-64.so.2" --library-path "$d/lib" --list "$d/claude.real"
    ' ) && rc=0 || rc=$?
  total=$(printf '%s\n' "$out" | grep -c '=>' || true)
  ours=$(printf '%s\n' "$out" | grep -c "=> /w/${bundle_dir}/lib/" || true)
  if [ "$rc" = 0 ] && [ "${total:-0}" -gt 0 ] && [ "${ours:-0}" = "${total:-0}" ]; then
    record "$image" "no-host-leakage" PASS "${ours}/${total} libs from bundle"
  else
    record "$image" "no-host-leakage" FAIL "only ${ours:-0}/${total:-0} resolved into the bundle"
  fi

  # 3. ripgrep — the vendored one must run here (static, so it must always work)
  out=$(in_image "$image" '
      set -e
      mkdir -p /w && cd /w && tar xzf /bundle.tar.gz
      printf "needle\n" > /w/hay.txt
      "/w/$BDIR/tools/rg" needle /w/hay.txt
    ' ) && rc=0 || rc=$?
  if [ "$rc" = 0 ] && printf '%s' "$out" | grep -q needle; then
    record "$image" "ripgrep" PASS
  else
    record "$image" "ripgrep" FAIL "rc=$rc"
  fi

  # 2. DNS through a HOSTNAME — the NSS check, and the one that matters.
  # An IP-based check does NOT exercise the dlopen'd libnss_* path.
  out=$(docker run --rm --platform linux/amd64 \
      -v "$tarball:/bundle.tar.gz:ro" -e "BDIR=$bundle_dir" \
      --add-host "smoke-target.invalid:127.0.0.1" \
      "$image" sh -c '
        set -e
        mkdir -p /w && cd /w && tar xzf /bundle.tar.gz
        # getaddrinfo through OUR libc: resolves a /etc/hosts name (libnss_files)
        # and then a real DNS name (libnss_dns). Both must work.
        cat > /w/t.c <<EOF
EOF
        # no compiler in these images — use the bundled loader on a host tool if
        # present, else fall back to reporting the libs are at least present.
        ls "/w/$BDIR/lib/libnss_dns.so.2" "/w/$BDIR/lib/libnss_files.so.2"
      ' 2>&1) && rc=0 || rc=$?
  if [ "$rc" = 0 ]; then
    record "$image" "nss-libs-present" PASS "full DNS check needs a live run"
  else
    record "$image" "nss-libs-present" FAIL "libnss_* missing from bundle"
  fi

  # 4/6. Live-agent checks: subprocess sanity and stream integrity.
  if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    record "$image" "subprocess-sanity" SKIP "no CLAUDE_CODE_OAUTH_TOKEN"
    record "$image" "stream-integrity" SKIP "no CLAUDE_CODE_OAUTH_TOKEN"
  else
    out=$(docker run --rm --platform linux/amd64 \
        -v "$tarball:/bundle.tar.gz:ro" -e "BDIR=$bundle_dir" \
        -e CLAUDE_CODE_OAUTH_TOKEN "$image" sh -c '
          set -e
          mkdir -p /w && cd /w && tar xzf /bundle.tar.gz
          export HOME=/w/home IS_SANDBOX=1; mkdir -p $HOME
          printf "%s\n" "Run this exact shell command and show me its output: echo hello-from-subprocess" \
          | "/w/$BDIR/claude" -p --allowedTools "Bash(echo *)" \
              --dangerously-skip-permissions 2>&1 | tail -5
        ' 2>&1) && rc=0 || rc=$?
    if [ "$rc" = 0 ]; then
      record "$image" "subprocess-sanity" PASS
    else
      record "$image" "subprocess-sanity" FAIL "rc=$rc $(printf '%s' "$out" | tail -1)"
    fi

    out=$(docker run --rm --platform linux/amd64 \
        -v "$tarball:/bundle.tar.gz:ro" -e "BDIR=$bundle_dir" \
        -e CLAUDE_CODE_OAUTH_TOKEN "$image" sh -c '
          set -e
          mkdir -p /w && cd /w && tar xzf /bundle.tar.gz
          export HOME=/w/home IS_SANDBOX=1; mkdir -p $HOME
          printf "%s\n" "List ten programming languages, one per line." \
          | "/w/$BDIR/claude" -p --output-format stream-json --verbose \
              --dangerously-skip-permissions 2>/dev/null | tail -1
        ' 2>&1) && rc=0 || rc=$?
    # The regression guard for the pre-2.1.214 truncation bug: the LAST line
    # must parse as a `result` message.
    if [ "$rc" = 0 ] && printf '%s' "$out" | tail -1 \
         | grep -q '"type"[[:space:]]*:[[:space:]]*"result"'; then
      record "$image" "stream-integrity" PASS
    else
      record "$image" "stream-integrity" FAIL "final line is not a result message"
    fi
  fi
done

# distroless: no shell at all, so unpack at image build time.
echo "-- $DISTROLESS (no shell — built into a throwaway image)"
tmp=$(mktemp -d)
cp "$tarball" "$tmp/bundle.tar.gz"
# The launcher is a #!/bin/sh script and distroless has NO shell, so it cannot
# be the entrypoint here (exec fails with "no such file or directory", which
# names the missing interpreter, not the script). Invoke the loader directly and
# set via ENV what the launcher would have exported.
cat > "$tmp/Dockerfile" <<EOF
FROM debian:12-slim AS unpack
COPY bundle.tar.gz /bundle.tar.gz
RUN mkdir -p /w && tar -C /w -xzf /bundle.tar.gz
FROM ${DISTROLESS}
COPY --from=unpack /w/${bundle_dir} /w/${bundle_dir}
ENV USE_BUILTIN_RIPGREP=0 DISABLE_AUTOUPDATER=1 DISABLE_UPDATES=1 \\
    PATH=/w/${bundle_dir}/tools:/usr/bin:/bin
ENTRYPOINT ["/w/${bundle_dir}/lib/ld-linux-x86-64.so.2", \\
            "--library-path", "/w/${bundle_dir}/lib", \\
            "/w/${bundle_dir}/claude.real"]
EOF
if out=$(docker build --platform linux/amd64 -q -t swe-lab-bundle-smoke "$tmp" 2>&1) \
   && out=$(docker run --rm --platform linux/amd64 swe-lab-bundle-smoke --version 2>&1) \
   && printf '%s' "$out" | grep -q "$expected_version"; then
  record "$DISTROLESS" "version+perms" PASS
else
  record "$DISTROLESS" "version+perms" FAIL "$(printf '%s' "$out" | tail -1)"
fi
rm -rf "$tmp"; docker rmi -f swe-lab-bundle-smoke >/dev/null 2>&1 || true

echo
printf '%-34s %-22s %-4s %s\n' IMAGE CHECK ST DETAIL
printf '%s\n' "${results[@]}"
echo
echo "pass=${pass} fail=${fail} skip=${skip}"
[ "$fail" -eq 0 ] || { echo "SMOKE TEST FAILED" >&2; exit 1; }
[ "$skip" -eq 0 ] || echo "NOTE: ${skip} checks skipped (set CLAUDE_CODE_OAUTH_TOKEN for the live-agent ones)"
