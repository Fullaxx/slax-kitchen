#!/bin/sh
# Refuse a release whose tag and version disagree.
#
#   ci/release-guard.sh <tag> [--tag-push]
#
# Exit 0 = safe to publish. Exit 1 = do not. Runs in under a second and holds the
# release workflow's first job, so a mismatch costs nothing rather than seventeen
# minutes of CI followed by a bad Release object that has to be deleted by hand.
#
# KITCHEN_VERSION lives at kitchen:8 and nothing has ever checked it. There are no
# tags in this repo yet, so the first one is also the first chance to get this wrong.
#
# --tag-push enables the rules that only make sense for a real `git push --tags`.
# Without it the script is still fully enforcing, which is what makes a dispatch
# dry-run against v0.1.0-dev a genuine rehearsal rather than a different code path.
set -u
# Overridable, the same way ci/lib.sh does it -- which is what lets
# tests/unit/test_release.py point this at a throwaway repo instead of
# asserting against whatever KITCHEN_VERSION happens to be today.
: "${REPO_ROOT:=$(cd "$(dirname "$0")/.." && pwd)}"
TAG=${1:-}
TAG_PUSH=0
[ "${2:-}" = "--tag-push" ] && TAG_PUSH=1

[ -n "$TAG" ] || { echo "usage: ci/release-guard.sh <tag> [--tag-push]" >&2; exit 2; }

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    G=$(printf '\033[32m'); R=$(printf '\033[31m'); O=$(printf '\033[0m')
else G=; R=; O=; fi

rc=0
ok()   { printf '  %sok%s   %s\n' "$G" "$O" "$*"; }
bad()  { printf '  %sFAIL%s %s\n' "$R" "$O" "$*" >&2; rc=1; }

VERSION=$(sed -n 's/^KITCHEN_VERSION="\(.*\)"$/\1/p' "$REPO_ROOT/kitchen" | head -1)
[ -n "$VERSION" ] || { echo "release-guard: could not read KITCHEN_VERSION from kitchen" >&2; exit 2; }

printf 'release guard  tag=%s  KITCHEN_VERSION=%s\n' "$TAG" "$VERSION"

# --- 1. the tag names the version the tree claims to be ---------------------
# Always enforced. This is the invariant: a Release called v0.2.0 whose `kitchen
# version` says 0.1.0 is worse than no release, because the bug reports it
# generates point at the wrong tree.
if [ "$TAG" = "v$VERSION" ]; then
    ok "tag matches kitchen:8"
else
    bad "tag is $TAG but kitchen:8 says $VERSION (expected tag v$VERSION)"
fi

# --- 2. a published release is not a development version --------------------
# Tag pushes only. A dry run exists precisely so the whole path can be rehearsed
# while the tree is still 0.1.0-dev.
case "$VERSION" in
    *-dev)
        if [ "$TAG_PUSH" = 1 ]; then
            bad "KITCHEN_VERSION is $VERSION -- drop the -dev suffix before tagging"
        else
            ok "KITCHEN_VERSION is a development version (not a tag push, so allowed)"
        fi ;;
    *)  ok "KITCHEN_VERSION is not a development version" ;;
esac

# --- 3. the commit being released is on master ------------------------------
# A tag on a feature branch would publish a tree that never passed branch
# protection. Skipped, loudly, when the ref is not available -- a shallow clone
# or a fresh fork has no origin/master to compare against, and silently passing
# a check that could not run is how upstream-watch stayed inert for four runs.
HEAD_SHA=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo)
if [ -z "$HEAD_SHA" ]; then
    bad "not a git checkout"
elif git -C "$REPO_ROOT" rev-parse --verify -q origin/master >/dev/null 2>&1; then
    if git -C "$REPO_ROOT" merge-base --is-ancestor "$HEAD_SHA" origin/master 2>/dev/null; then
        ok "$(echo "$HEAD_SHA" | cut -c1-7) is on origin/master"
    else
        bad "$(echo "$HEAD_SHA" | cut -c1-7) is not an ancestor of origin/master"
    fi
elif [ "$TAG_PUSH" = 1 ]; then
    # On a real release this is not a skippable check: publishing a tree that never
    # passed branch protection is exactly what it exists to stop. `actions/checkout`
    # needs fetch-depth: 0 for the ref to be here at all.
    bad "origin/master not fetched -- cannot verify the commit is on master"
else
    printf '  %sskip%s origin/master not fetched -- cannot check the commit is on master\n' "$R" "$O" >&2
fi

if [ "$rc" -eq 0 ]; then
    printf '%srelease guard passed%s\n' "$G" "$O"
else
    printf '%srelease guard failed%s -- nothing was published\n' "$R" "$O" >&2
fi
exit $rc
