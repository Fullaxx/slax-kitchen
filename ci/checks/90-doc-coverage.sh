#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Every recipe has a page, is linked from the index, and the prose count is right.
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

# ...and is linked from the section index. The gate used to check only that the FILE
# existed, so chromium-current and firefox-esr shipped with pages nobody could reach
# from the cookbook front page -- which is the same failure as having no page.
INDEX="$COOKBOOK/README.md"
[ -f "$INDEX" ] || fail "missing $INDEX"
for r in "$RECIPES"/*.yaml; do
    [ -e "$r" ] || continue
    n=$(basename "$r" .yaml)
    grep -q "]($n\.md)" "$INDEX" || \
        fail "recipe is not linked from the cookbook index: $n -> docs/50-cookbook/README.md"
done

for d in "$COOKBOOK"/*.md; do
    [ -e "$d" ] || continue
    n=$(basename "$d" .md)
    # README.md is the section index, not a recipe page.
    [ "$n" = README ] && continue
    [ -f "$RECIPES/$n.yaml" ] || \
        fail "cookbook page has no recipe: docs/50-cookbook/$n.md -> recipes/available/$n.yaml"
done

# ...and the prose count matches reality. "Thirty recipes ship today" drifted to 29 in
# one file, 30 in another and 32 on disk before anyone noticed, then drifted again the
# next time a recipe landed. A number spelled out in words is exactly the kind of fact
# nobody thinks to re-check.
n=$(find "$RECIPES" -maxdepth 1 -name '*.yaml' | wc -l | tr -d ' ')
case "$n" in
    28) want=twenty-eight ;;  29) want=twenty-nine ;;
    30) want=thirty ;;        31) want=thirty-one ;;
    32) want=thirty-two ;;    33) want=thirty-three ;;
    34) want=thirty-four ;;   35) want=thirty-five ;;
    36) want=thirty-six ;;    37) want=thirty-seven ;;
    38) want=thirty-eight ;;  39) want=thirty-nine ;;
    40) want=forty ;;
    # Add the next word when you add the next recipe -- an unknown count skips the
    # check rather than failing, because a gate that blocks on its own lookup table
    # being short teaches people to disable it.
    *)  want= ; note "90-doc-coverage: no word for $n recipes; count check skipped" ;;
esac
if [ -n "$want" ]; then
    for f in "$REPO_ROOT/README.md" "$REPO_ROOT/docs/50-cookbook/README.md"; do
        [ -f "$f" ] || continue
        # Every "<Word> recipes ship today/across" must spell the real count.
        #
        # Collected into a variable rather than piped into `while read`: a pipeline runs
        # its last stage in a SUBSHELL, so fail() would set _FAILED=1 in a process that
        # then exits, printing FAIL and returning 0. This gate had exactly that bug for
        # the length of one test run.
        said=$(grep -oiE '[a-z-]+ recipes ship (today|across)' "$f" \
               | awk '{print tolower($1)}' | sort -u)
        for w in $said; do
            [ "$w" = "$want" ] || \
                fail "${f#"$REPO_ROOT"/}: says '$w recipes ship', but $n recipes ship (want '$want')"
        done
    done
fi

check_result
