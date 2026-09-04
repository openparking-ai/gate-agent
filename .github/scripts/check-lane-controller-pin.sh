#!/usr/bin/env bash
# The lane-controller pin must name a commit on lane-controller's MAIN branch,
# AND the contract version at that commit -- and the one on main -- must be a
# version this package reads.
#
# Copied from `lane-controller/.github/scripts/check-vehicle-id-pin.sh`, which
# exists because a pin there once named a commit that lived only on a feature
# branch: squash-merging and deleting that branch would have made it
# unresolvable and broken `pip install` for everyone.
#
# It bites HARDER here. The pin is a TEST dependency, so what breaks is not the
# package -- it is the suite, and a suite that cannot run is the quietest way for
# a guarantee to stop being one. `openparking-lane-controller` is what serves a
# REAL lane in these tests, and the whole seat proof rests on it.
#
# THE VERSION HALF IS HERE BECAUSE ANCESTRY ALONE WENT GREEN ON THE DAY IT
# MATTERED. On 2026-08-31 `lane-controller` merged its round 6 and its contract
# went from 1 to 2. This package still read `(1,)`, so its monitor and its whole
# capture process refused to start against that lane and its agent read every
# lane as `lane_unavailable` -- and this check stayed GREEN throughout, measured
# after the merge, because the pinned commit was still an ancestor of main. An
# ancestor is not a version. A check that passes for the wrong reason has
# failed, so it now asks the question that was actually wrong:
#
#   * the version at the PINNED commit must be one this package reads -- or the
#     suite is running against a lane the code cannot interpret;
#   * the version on lane-controller MAIN must be one this package reads too --
#     because a pin that lags a version bump is exactly the state above, and the
#     pin being an ancestor is what makes it look fine.
#
# A comment saying "re-pin before merging" is not a check. This is.
set -euo pipefail

CONTRACT=src/gate_agent/contract.py

PIN=$(grep -oE 'openparking-lane-controller @ git\+https://github\.com/openparking-ai/lane-controller@[0-9a-f]{40}' pyproject.toml \
      | grep -oE '[0-9a-f]{40}$' || true)

if [ -z "$PIN" ]; then
  echo "no 40-character lane-controller commit pin found in pyproject.toml"
  echo "the dependency must be pinned to an exact commit, not a branch or a tag"
  exit 1
fi

echo "pinned lane-controller commit: $PIN"

# The ONE set, read out of the one file that defines it. Read rather than typed
# here for the same reason it is defined once there: a second copy in this
# script would be a fourth place to forget.
KNOWN=$(grep -oE '^KNOWN_LANE_VERSIONS: tuple\[int, \.\.\.\] = \([0-9, ]*\)' "$CONTRACT" \
        | grep -oE '\([0-9, ]*\)' || true)

if [ -z "$KNOWN" ]; then
  echo "FAIL: no KNOWN_LANE_VERSIONS assignment found in $CONTRACT."
  echo "this check reads the set from there; if it has moved, move this with it."
  exit 1
fi

# `(1,)` and `(1, 2)` both become ` 1 ` / ` 1 2 `, so a version can be matched
# with a whole-word test and `1` never matches inside `12`.
KNOWN_LIST=$(echo "$KNOWN" | tr -d '(),' | tr -s ' ' | sed -e 's/^ *//' -e 's/ *$//')
echo "this package reads lane contract version(s): $KNOWN_LIST"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git clone --quiet --filter=blob:none --no-checkout \
    https://github.com/openparking-ai/lane-controller.git "$TMP/lane-controller"

if ! git -C "$TMP/lane-controller" cat-file -e "$PIN^{commit}" 2>/dev/null; then
  echo "FAIL: $PIN does not exist in openparking-ai/lane-controller at all."
  exit 1
fi

# Read the version AT A COMMIT, out of the file that defines it there. Not from
# the installed package: this has to be answerable about `origin/main`, which is
# not installed anywhere and is the half the ancestry check could never see.
version_at() {
  git -C "$TMP/lane-controller" show "$1:src/lane_controller/contract.py" 2>/dev/null \
    | grep -oE '^CONTRACT_VERSION = [0-9]+' | grep -oE '[0-9]+$' || true
}

reads_it() {
  for known in $KNOWN_LIST; do
    [ "$known" = "$1" ] && return 0
  done
  return 1
}

PIN_VERSION=$(version_at "$PIN")
MAIN_VERSION=$(version_at origin/main)

if [ -z "$PIN_VERSION" ] || [ -z "$MAIN_VERSION" ]; then
  echo "FAIL: could not read CONTRACT_VERSION from src/lane_controller/contract.py"
  echo "      at the pin (got '${PIN_VERSION:-nothing}') or on main (got '${MAIN_VERSION:-nothing}')."
  echo "      A version this check cannot read is not a version it has checked."
  exit 1
fi

echo "lane contract version at the pin: $PIN_VERSION;  on lane-controller main: $MAIN_VERSION"

FAILED=0
if ! reads_it "$PIN_VERSION"; then
  echo "FAIL: the PINNED commit speaks lane contract version $PIN_VERSION, and this package"
  echo "      reads $KNOWN_LIST. The suite would be running against a lane whose payloads this"
  echo "      code refuses -- every consumer here would report only that the lane cannot be read."
  FAILED=1
fi
if ! reads_it "$MAIN_VERSION"; then
  echo "FAIL: lane-controller MAIN speaks lane contract version $MAIN_VERSION, and this package"
  echo "      reads $KNOWN_LIST. This is the state the ancestry check below cannot see: the pin"
  echo "      is still an ancestor of main and everything looks fine, while a real lane built"
  echo "      from main is unreadable to this build. Bump KNOWN_LANE_VERSIONS in $CONTRACT and"
  echo "      re-pin, or say in the receipt why this build must not read main's lane."
  FAILED=1
fi
if [ "$FAILED" = 1 ]; then
  exit 1
fi

if git -C "$TMP/lane-controller" merge-base --is-ancestor "$PIN" origin/main 2>/dev/null; then
  echo "OK: the pin is an ancestor of lane-controller main, and both versions are read here."
  exit 0
fi

echo "FAIL: $PIN is NOT on lane-controller main. It is reachable only from:"
git -C "$TMP/lane-controller" branch -r --contains "$PIN" | sed 's/^/    /'
cat <<'MSG'

A pin to a commit that lives only on a feature branch stops resolving the
moment that branch is deleted, and `pip install` of this package breaks.

Merge order:
    1. merge lane-controller's pull request
    2. re-pin pyproject.toml to the resulting commit on lane-controller main
    3. merge this one
MSG
exit 1
