#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Every cookbook page declares one rung of the verification ladder, by name.
#
# For a long time 23 of the 30 pages said "**Status: verified**", which meant six
# different things: schema-validated, built on one target, built on four, booted once
# by hand, booted with markers, or actually watched working. A reader could not tell
# which, and neither could we.
#
# The ladder is defined in CONTRIBUTING.md. Claiming a rung you did not reach is the
# one thing this project treats as a real error, so the vocabulary is worth enforcing
# even though the honesty behind it cannot be.
#
# Whole-tree by design: the value of a shared vocabulary is that it holds everywhere,
# and a new page is exactly the one most likely to invent its own word.
. "$(dirname "$0")/../lib.sh"

COOKBOOK="$REPO_ROOT/docs/50-cookbook"
[ -d "$COOKBOOK" ] || fail "missing $COOKBOOK"

# In ladder order. "artifact boot-verified" must be tried before "boot-verified".
RUNGS="schema-valid gate-clean matrix-verified artifact-boot-verified boot-verified runtime-verified"

for d in "$COOKBOOK"/*.md; do
    [ -e "$d" ] || continue
    rel="docs/50-cookbook/$(basename "$d")"
    # README.md is the section index, not a recipe page.
    [ "$(basename "$d" .md)" = README ] && continue

    line=$(grep -m1 '^\*\*Status: ' "$d")
    if [ -z "$line" ]; then
        fail "$rel: no '**Status: <rung>**' line (the ladder is in CONTRIBUTING.md)"
        continue
    fi

    # Everything between "**Status: " and the closing "**".
    claim=${line#\*\*Status: }
    claim=${claim%%\*\**}
    # RUNGS is space-separated, so the two-word rung is spelled with a hyphen there
    # and translated back here.
    hit=0
    for r in $RUNGS; do
        case "$r" in
            artifact-boot-verified) want="artifact boot-verified" ;;
            *)                      want=$r ;;
        esac
        [ "$claim" = "$want" ] && { hit=1; break; }
    done
    [ "$hit" = 1 ] || fail "$rel: '$claim' is not a rung of the ladder (want one of:
        schema-valid, gate-clean, matrix-verified, artifact boot-verified,
        boot-verified, runtime-verified -- see CONTRIBUTING.md)"
done

check_result
