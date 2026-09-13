#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Every shipped recipe has a cookbook page, and every page has a recipe.
#
# A recipe nobody can find is not shipped, and a page describing a recipe that no
# longer exists is worse than no page. This gate was added after `serial-console`
# shipped for weeks with no docs/50-cookbook/ page and nothing noticed -- the original
# plan listed the check as a required PR gate but it was never written.
#
# Whole-tree by design: a rename touches two directories, and checking only staged
# files would pass a commit that moves one half.
. "$(dirname "$0")/../lib.sh"

RECIPES="$REPO_ROOT/recipes/available"
COOKBOOK="$REPO_ROOT/docs/50-cookbook"

[ -d "$RECIPES" ]  || fail "missing $RECIPES"
[ -d "$COOKBOOK" ] || fail "missing $COOKBOOK"

for r in "$RECIPES"/*.yaml; do
    [ -e "$r" ] || continue
    n=$(basename "$r" .yaml)
    [ -f "$COOKBOOK/$n.md" ] || \
        fail "recipe has no cookbook page: recipes/available/$n.yaml -> docs/50-cookbook/$n.md"
done

for d in "$COOKBOOK"/*.md; do
    [ -e "$d" ] || continue
    n=$(basename "$d" .md)
    # README.md is the section index, not a recipe page.
    [ "$n" = README ] && continue
    [ -f "$RECIPES/$n.yaml" ] || \
        fail "cookbook page has no recipe: docs/50-cookbook/$n.md -> recipes/available/$n.yaml"
done

check_result
