#!/bin/sh
# Build the body of a GitHub Release, on stdout.
#
#   ci/release-notes.sh <tag> [--run-url URL]
#
# Deterministic and inspectable locally, which is why it is not `gh --generate-notes`:
# the interesting half of these notes is the verification claim, and that has to be
# written by something that knows what this project's CI actually runs.
#
# A RELEASE OF THIS REPOSITORY IS THE TOOLKIT, not an image. No ISO is attached, so the
# notes say so, and point at NOTICE.md for what travels with an image when one IS
# published. This paragraph used to be a refusal -- "part of it cannot be satisfied from
# this repository ... you build the ISO" -- which read a note written for forks as a rule
# for this repo, and a unit test pinned the refusal word for word. The attachment claim is
# now checked against what release.yml actually uploads (tests/unit/test_release.py), so
# the day the workflow attaches something, this text has to change with it.
set -u
# Overridable, the same way ci/lib.sh does it -- which is what lets
# tests/unit/test_release.py point this at a throwaway repo instead of
# asserting against whatever KITCHEN_VERSION happens to be today.
: "${REPO_ROOT:=$(cd "$(dirname "$0")/.." && pwd)}"
TAG=${1:-}
RUN_URL=""
[ "${2:-}" = "--run-url" ] && RUN_URL=${3:-}
[ -n "$TAG" ] || { echo "usage: ci/release-notes.sh <tag> [--run-url URL]" >&2; exit 2; }

# Before the cd: a relative RELEASE_ASSETS is relative to where the CALLER stands.
case "${RELEASE_ASSETS:-}" in "" | /*) ;; *) RELEASE_ASSETS="$PWD/$RELEASE_ASSETS" ;; esac

cd "$REPO_ROOT" || exit 2

VERSION=$(sed -n 's/^KITCHEN_VERSION="\(.*\)"$/\1/p' kitchen | head -1)
SHA=$(git rev-parse HEAD 2>/dev/null || echo unknown)

# Absolute links, not relative ones. A relative link in a Release body resolves
# against .../releases/, which happens to work -- but "happens to work" is not
# something to verify by publishing a release and looking.
SLUG=${GITHUB_REPOSITORY:-}
if [ -z "$SLUG" ]; then
    SLUG=$(git remote get-url origin 2>/dev/null \
           | sed -e 's#^git@github.com:##' -e 's#^https://github.com/##' -e 's#\.git$##')
fi
BLOB="https://github.com/${SLUG:-Fullaxx/slax-kitchen}/blob/$TAG"

# THE REDISTRIBUTION SECTION. A release of this repository attaches no image, and says
# so. A project that publishes an image assembles its assets with ci/release-assets.sh and
# sets RELEASE_ASSETS to that directory; the section is then generated from the directory
# by ci/redistribution-claim.py, so it names what is attached rather than promising it.
if [ -n "${RELEASE_ASSETS:-}" ]; then
    REDISTRIBUTION=$(python3 "$REPO_ROOT/ci/redistribution-claim.py" "$RELEASE_ASSETS") || exit 1
    ATTACHED=$(python3 -c 'import json,sys
print("yes" if ((json.load(open(sys.argv[1])).get("image") or {}).get("attached")) else "no")' \
        "$RELEASE_ASSETS/release-index.json") || exit 1
else
    REDISTRIBUTION="## Redistribution

No image is attached to this release: a release of slax-kitchen is the toolkit.

A Slax ISO is an aggregate — Debian or Slackware packages, non-free firmware, and Slax's
own kernel and initramfs, each under its own terms. What travels with an image built with
this toolkit when one is published — the source of what the build compiled or modified,
where each upstream publishes its own, the firmware terms, and an identity that is not an
official Slax release — is set out in [NOTICE.md]($BLOB/NOTICE.md).

Slax and Linux Live Kit are the work of **Tomáš Matějíček** — <https://www.slax.org>.
This project customizes his work; it is not the project's home. If you find it useful,
support Slax upstream."
fi

if [ "${ATTACHED:-no}" = yes ]; then
    IMAGE_CHECKSUM_NOTE="The attached image's checksum is in \`SHA256SUMS\`, which checks the download. It does
not promise that a rebuild matches, because **images are not byte-reproducible**:
\`genisoimage\` varies both the volume timestamps and the extent order, and an identical
tree rebuilt elsewhere has been measured differing in 99.9% of its sectors. See
[reproducibility]($BLOB/docs/40-workflow/reproducibility.md)."
else
    IMAGE_CHECKSUM_NOTE="No image is attached to this release, so there is no image checksum here. Where an image
*is* published, its \`SHA256SUMS\` checks the download. It does not promise that a rebuild
matches, because **images are not byte-reproducible**: \`genisoimage\` varies both the
volume timestamps and the extent order, and an identical tree rebuilt elsewhere has been
measured differing in 99.9% of its sectors. See
[reproducibility]($BLOB/docs/40-workflow/reproducibility.md)."
fi

# The tag being released is usually not yet an object (the workflow runs on the ref,
# but a dry run has no tag at all), so walk back from the previous tag if there is
# one and from the root commit if there is not. --sort=-creatordate rather than
# alphabetical: v0.10.0 sorts before v0.9.0 as a string.
PREV=$(git tag --sort=-creatordate --list 'v*' | grep -v "^${TAG}\$" | head -1)
if [ -n "$PREV" ]; then
    RANGE="$PREV..HEAD"
    SINCE="since \`$PREV\`"
else
    RANGE="HEAD"
    SINCE="since the first commit -- this is the first tagged release"
fi

# A shallow clone has no history to log, and `git log` does not complain about it --
# it just returns what was fetched. Caught by the unit test running under CI's own
# `gates` job, which checks out at the default depth of 1: the changelog came out as
# a single line with nothing to say it was not the whole story. release.yml uses
# fetch-depth: 0, so the real path is fine; a wrong one should still say so.
SHALLOW=""
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = true ]; then
    SHALLOW="

> **This changelog is incomplete.** It was generated from a shallow clone, so it lists
> only the commits that were fetched. Re-run with \`fetch-depth: 0\`."
fi

# Capped, but never silently: a truncated changelog that says nothing about being
# truncated is worse than a long one.
LOG_CAP=${LOG_CAP:-100}    # overridable so the truncation path can be tested
NCOMMITS=$(git log --no-merges --oneline "$RANGE" 2>/dev/null | wc -l | tr -d ' ')
if [ "${NCOMMITS:-0}" -gt "$LOG_CAP" ]; then
    MORE="

<sub>… and $((NCOMMITS - LOG_CAP)) more. Full list: \`git log --no-merges $RANGE\`</sub>"
else
    MORE=""
fi

RECIPES=$(find recipes -name '*.yaml' 2>/dev/null | wc -l | tr -d ' ')
GATES=$(find ci/checks -name '*.sh' 2>/dev/null | wc -l | tr -d ' ')

# TIER C: DERIVED, NOT ASSERTED. This paragraph used to be a constant -- "Tier C was not
# run" printed into every release whether or not that was still true -- and a unit test
# pinned the constant, so the notes could never start telling the truth about a Tier C
# run that HAD happened. tests/boot/tier-c.json is written by a KVM host (CI has no
# /dev/kvm and never can), validated by ci/checks/97-tier-c-ledger.sh, and read here.
#
# Same shape as SHALLOW above: a condition that is usually one way, stated either way,
# with the negative case spelled out rather than assumed.
TIERC_LEDGER=${TIERC_LEDGER:-tests/boot/tier-c.json}
if [ -f "$TIERC_LEDGER" ] && command -v python3 >/dev/null 2>&1; then
    TIERC=$(python3 "$REPO_ROOT/ci/tier-c-claim.py" "$TIERC_LEDGER") || TIERC=""
fi
if [ -z "${TIERC:-}" ]; then
    TIERC="**Tier C was not run.** The full boot matrix -- BIOS menu, UEFI, USB image,
persistence -- needs \`/dev/kvm\`, which GitHub-hosted runners do not have.

All four targets are matrix-verified, not boot-verified. They build and their structure
is correct; they were not booted.

No release claims a desktop came up. Tier C asserts that every boot path reaches
\`Live Kit done\` and assembles the filesystem it should; a person looking at Fluxbox is
\`runtime-verified\`, which is a different rung."
fi

cat <<EOF
\`kitchen\` $VERSION — \`$(echo "$SHA" | cut -c1-7)\`

## Changes

$(git log --no-merges --pretty='- %s' "$RANGE" 2>/dev/null | head -"$LOG_CAP")$MORE$SHALLOW

<sub>$SINCE</sub>

## What was verified

Rungs are the ones defined in [CONTRIBUTING.md]($BLOB/CONTRIBUTING.md#say-what-you-actually-verified),
and mean exactly what they say there.

| | |
|---|---|
| **gate-clean** | all $GATES commit gates |
| **matrix-verified** | every compatible recipe applied individually to all four targets, then structure-asserted — \`debian-{32,64}bit-12.2.0\`, \`slackware-{32,64}bit-15.0.4\`. A release runs the FULL matrix: the per-push path skips \`ci/slow-recipes.txt\`, a tag does not. |
| **boot-verified** | \`debian-64bit-12.2.0\` only: direct-kernel QEMU boot under TCG, all three livekit markers, plus \`union: aufs\` and the dpkg package count |

$TIERC
$([ -n "$RUN_URL" ] && printf '\nCI run: %s' "$RUN_URL")

## Provenance

Commit \`$SHA\`.

Base ISOs this was built against — these hashes *are* verifiable, and \`kitchen fetch\`
enforces both size and sha256 on every download:

$(sed -n '/^targets:/,$p' compat/sources.yaml \
  | awk '/^  [a-z]/ {t=$1; sub(":","",t)} /sha256:/ {printf "- `%s` `%s`\n", t, $2}')

$RECIPES recipes, $GATES gates. \`kitchen doctor --report\` on the build machine records the
tool versions.

$IMAGE_CHECKSUM_NOTE

$REDISTRIBUTION
EOF
