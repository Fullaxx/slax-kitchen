#!/bin/sh
# Run every check registered for a stage.
#
#   ci/run-checks.sh <pre-commit|pre-push|ci> [scope]
#
# scope defaults to "staged" for pre-commit and "tree" otherwise.
# Hooks and CI both call this -- there is exactly one implementation of every gate.
set -u
STAGE=${1:-pre-commit}
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
export REPO_ROOT

case "$STAGE" in
    pre-commit) DEFAULT_SCOPE=staged ;;
    pre-push|ci) DEFAULT_SCOPE=tree ;;
    *) echo "unknown stage: $STAGE (want pre-commit|pre-push|ci)" >&2; exit 2 ;;
esac
KITCHEN_SCOPE=${2:-$DEFAULT_SCOPE}
export KITCHEN_SCOPE

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    B=$(printf '\033[1m'); R=$(printf '\033[31m'); G=$(printf '\033[32m')
    D=$(printf '\033[2m'); O=$(printf '\033[0m')
else B=; R=; G=; D=; O=; fi

printf '%sslax-kitchen checks%s  stage=%s scope=%s\n' "$B" "$O" "$STAGE" "$KITCHEN_SCOPE"

rc=0 ran=0 skipped=0
for chk in "$REPO_ROOT"/ci/checks/*.sh; do
    [ -f "$chk" ] || continue
    name=$(basename "$chk" .sh)
    grep -q "^# stages:.*\b$STAGE\b" "$chk" || { skipped=$((skipped+1)); continue; }
    desc=$(sed -n 's/^# desc: //p' "$chk" | head -1)
    ran=$((ran+1))
    if sh "$chk"; then
        printf '  %sok%s   %-24s %s%s%s\n' "$G" "$O" "$name" "$D" "$desc" "$O"
    else
        printf '  %sFAIL%s %-24s %s%s%s\n' "$R" "$O" "$name" "$D" "$desc" "$O"
        rc=1
    fi
done

if [ "$rc" -eq 0 ]; then
    printf '%s%d checks passed%s (%d not in this stage)\n' "$G" "$ran" "$O" "$skipped"
else
    printf '%schecks failed%s -- fix the above, or bypass with git commit --no-verify\n' "$R" "$O"
    printf '%s(CI re-runs every gate, so --no-verify only defers the failure)%s\n' "$D" "$O"
fi
exit $rc
