#!/usr/bin/env bash
# The lane-controller pin must name a commit on lane-controller's MAIN branch.
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
# A comment saying "re-pin before merging" is not a check. This is.
set -euo pipefail

PIN=$(grep -oE 'openparking-lane-controller @ git\+https://github\.com/openparking-ai/lane-controller@[0-9a-f]{40}' pyproject.toml \
      | grep -oE '[0-9a-f]{40}$' || true)

if [ -z "$PIN" ]; then
  echo "no 40-character lane-controller commit pin found in pyproject.toml"
  echo "the dependency must be pinned to an exact commit, not a branch or a tag"
  exit 1
fi

echo "pinned lane-controller commit: $PIN"

TMP=$(mktemp -d)
git clone --quiet --filter=blob:none --no-checkout \
    https://github.com/openparking-ai/lane-controller.git "$TMP/lane-controller"

if ! git -C "$TMP/lane-controller" cat-file -e "$PIN^{commit}" 2>/dev/null; then
  echo "FAIL: $PIN does not exist in openparking-ai/lane-controller at all."
  exit 1
fi

if git -C "$TMP/lane-controller" merge-base --is-ancestor "$PIN" origin/main 2>/dev/null; then
  echo "OK: the pin is an ancestor of lane-controller main."
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
