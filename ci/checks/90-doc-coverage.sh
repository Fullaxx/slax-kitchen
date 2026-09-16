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
# The lookup table is shared by both count checks below. An unknown count yields the
# empty string and the caller skips its check rather than failing, because a gate that
# blocks on its own table being short teaches people to disable it.
numword() {
    case "$1" in
        1) echo one ;;            2) echo two ;;
        3) echo three ;;          4) echo four ;;
        5) echo five ;;           6) echo six ;;
        7) echo seven ;;          8) echo eight ;;
        9) echo nine ;;          10) echo ten ;;
        11) echo eleven ;;       12) echo twelve ;;
        13) echo thirteen ;;     14) echo fourteen ;;
        15) echo fifteen ;;      16) echo sixteen ;;
        17) echo seventeen ;;    18) echo eighteen ;;
        19) echo nineteen ;;     20) echo twenty ;;
        21) echo twenty-one ;;   22) echo twenty-two ;;
        23) echo twenty-three ;; 24) echo twenty-four ;;
        25) echo twenty-five ;;  26) echo twenty-six ;;
        27) echo twenty-seven ;; 28) echo twenty-eight ;;
        29) echo twenty-nine ;;  30) echo thirty ;;
        31) echo thirty-one ;;   32) echo thirty-two ;;
        33) echo thirty-three ;; 34) echo thirty-four ;;
        35) echo thirty-five ;;  36) echo thirty-six ;;
        37) echo thirty-seven ;; 38) echo thirty-eight ;;
        39) echo thirty-nine ;;  40) echo forty ;;
        *)  echo ;;
    esac
}

n=$(find "$RECIPES" -maxdepth 1 -name '*.yaml' | wc -l | tr -d ' ')
want=$(numword "$n")
[ -n "$want" ] || note "90-doc-coverage: no word for $n recipes; count check skipped"
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

# ...and the same for the GATE count, which is the identical failure with a third noun.
# Adding ci/checks/97-tier-c-ledger.sh took the tree from twelve gates to thirteen and
# instantly made six shipped files wrong -- CONTRIBUTING.md in four places, status.md,
# ci.md and containers/README.md. Every one of them was accurate when written. A number
# spelled out in words is exactly the fact nobody thinks to re-check, which is why the
# other two counts are already gated here.
n=$(find "$REPO_ROOT/ci/checks" -maxdepth 1 -name '*.sh' | wc -l | tr -d ' ')
want=$(numword "$n")
[ -n "$want" ] || note "90-doc-coverage: no word for $n gates; count check skipped"
if [ -n "$want" ]; then
    words='one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty'
    # Anchored, for the same reason the upstream-issue rule anchors on a link: "gates" is
    # an ordinary word here. The busybox replacement has its own five-gate harness, and
    # "had only three gates written" is a true sentence about a different set. A line is
    # claiming THIS count only if it also names the thing that runs them.
    anchor='commit gates|selftest|ci/checks|run-checks|doctor --strict'
    for f in $(check_files_nl | grep -E '\.md$' | grep -v '^vendor/'); do
        [ -f "$REPO_ROOT/$f" ] || continue
        hit=$(grep -niE "\b($words)\b[[:space:]]+(commit[[:space:]]+)?gates\b" \
              "$REPO_ROOT/$f" | grep -iE "$anchor" | grep -ivE "\b$want\b") || true
        [ -n "$hit" ] && fail "${f}: $(echo "$hit" | head -1) -- there are $n gates (want '$want')"
    done
fi

# ...and the same for the upstream-issue count, which is the identical failure with a
# different noun -- and a worse instance. Bugs 13 and 14 were appended months apart while
# the count lived in prose in four OTHER files, so by 2026-09-16 three files said twelve,
# two said thirteen, and the page listed fourteen. Nobody noticed for either append.
#
# The five claims are phrased five different ways ("Fourteen, recorded separately",
# "fourteen of them", "fourteen issues found during analysis", "there are fourteen
# documented", "Fourteen documented"), so matching a phrase cannot work. Anchoring on
# the LINK is what lets one rule cover all of them: a line that points a reader at the
# page and states a number is claiming that page's count.
BUGS=docs/30-inventory/known-upstream-bugs.md
if [ -f "$REPO_ROOT/$BUGS" ]; then
    n=$(grep -c '^## [0-9]' "$REPO_ROOT/$BUGS")
    want=$(numword "$n")
    [ -n "$want" ] || note "90-doc-coverage: no word for $n upstream issues; count check skipped"
    if [ -n "$want" ]; then
        words='one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty'
        # check_files_nl, not `grep -r`: it is git-scoped, so ignored working notes (which
        # quote these counts while discussing the drift) and vendor/ never reach the check.
        files=$(check_files_nl | grep -E '\.md$' | grep -v '^vendor/')
        bad=
        for f in $files; do
            [ -f "$REPO_ROOT/$f" ] || continue
            hit=$(grep -niE "known-upstream-bugs\.md" "$REPO_ROOT/$f" \
                  | grep -iE "\b($words)\b" \
                  | grep -ivE "\b$want\b") || true
            [ -n "$hit" ] && bad="$bad$f:$hit
"
        done
        if [ -n "$bad" ]; then
            # Loop in the CURRENT shell -- a `printf | while read` runs its body in a
            # subshell and fail()'s _FAILED=1 dies with it. That exact bug is documented
            # on the recipe-count check above; it is not repeated here.
            _oifs=$IFS; IFS='
'
            for l in $bad; do
                fail "$l  <- links known-upstream-bugs.md but does not say '$want' ($n issues)"
            done
            IFS=$_oifs
        fi
    fi
fi

check_result
