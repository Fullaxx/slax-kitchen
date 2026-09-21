#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Every recipe has a page, is linked from the index, and the prose counts are right.
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

# ...and the same for the TARGET count, which is the widest of the four. "four targets" is
# the build matrix, and on 2026-09-20 it was stated on 45 lines across 33 files with nothing
# checking any of them -- 41 as the words and 4 as the compound "4-target". A fifth target
# would have falsified 33 files in one commit and said nothing.
#
# DERIVED FROM WHAT THE FILES SAY THEY ARE, not from their names: compat/ also holds
# sources.yaml and upstream-baseline.yaml, curated by hand and not targets, so a *-*.yaml
# glob would count six. `kind: Fingerprint` is the declaration itself.
#
# PLURAL ONLY, and that is this rule's anchor -- it needs no other. The singular is the
# verification ladder's phrase, "booted on one target", which is not the matrix count and is
# a true sentence about a different thing. Plural separates the two with no word list to keep
# in step: measured 2026-09-20, 45 lines gated, 3 singular left alone, no false positives.
# The first legitimately local "the two targets that use GRUB" will fail this, and that is
# when to give it an exemption rather than now.
#
# BOTH FORMS, because both occur: the word in prose, the digit in "4-target matrix".
#
# AND THE NUMBER IS READ, NOT GREPPED FOR. The other two rules ask "does this line mention
# the right word anywhere", which exempts a line for containing it in an unrelated place --
# and `\bfour\b` matches inside "Twenty-four". docs/50-cookbook/README.md:116 opens
# "**Twenty-four of the thirty-five work on all four targets**", so the first draft of this
# rule read that line, found "four", and passed it whatever the target count said. Caught by
# planting a wrong number in that exact line rather than by reading the regex. So the token
# immediately before "targets" is extracted and compared, which is the question being asked.
# A hyphenated pair is one token: "twenty-four targets" must not read as "four".
n=$(grep -l '^kind: Fingerprint' "$REPO_ROOT"/compat/*.yaml 2>/dev/null | wc -l | tr -d ' ')
want=$(numword "$n")
[ -n "$want" ] || note "90-doc-coverage: no word for $n targets; count check skipped"
if [ -n "$want" ] && [ "$n" -gt 0 ]; then
    words='one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty'
    # ONE grep over every file, not one per file. "targets" is common enough that this rule
    # has to read all 122 of them, where the rules above narrow to a phrase first. Measured
    # 2026-09-20, median of three: the gate was 0.556 s with three rules, 0.837 s with this
    # one written per-file, and 0.639 s written this way -- so the loop cost 0.28 s and the
    # single grep costs 0.08, on a gate suite of about 35 s.
    # Absolute paths rather than `cd "$REPO_ROOT"`: a cd here would leak into the
    # upstream-issue rule below, and `return` outside a function is not a thing in sh.
    check_files_nl | grep -E '\.md$' | grep -v '^vendor/' \
        | sed "s|^|$REPO_ROOT/|" > /tmp/.kitchen-tgt-f.$$
    # -H, because grep OMITS the filename when it is given exactly one file, and the parse
    # below splits on it. Reachable: a tree holding only docs/50-cookbook/README.md lists one
    # file, and xargs may end a batch on one. Without -H that line parses as file="12",
    # line="four targets" and the rule reports nonsense instead of a finding.
    #
    # AN EMPTY LIST IS NORMAL HERE, and refusing it blocked every code-only commit. This
    # said an empty list "cannot fire in practice, which is exactly why it would never be
    # noticed if it could" -- and then it fired, at pre-commit, on a commit that staged
    # twenty-five .py files and no markdown. The reasoning conflated two different empty
    # sets: ci/lib.sh FATALs when GIT will not answer, which is the unobtainable list this
    # was guarding against, while a staged scope holding no .md is an ordinary Python
    # change. CI never saw it, because `ci` runs scope=tree where markdown always exists.
    #
    # So it notes and skips, like the four other count rules in this file, all of which
    # already say "count check skipped" rather than failing when they cannot answer.
    if [ -s /tmp/.kitchen-tgt-f.$$ ]; then
        xargs grep -noHiE "(($words)(-($words))?|[0-9]+)([[:space:]]+targets|-targets?)" \
            < /tmp/.kitchen-tgt-f.$$ > /tmp/.kitchen-tgt.$$ 2>/dev/null || true
    else
        note "90-doc-coverage: no markdown in scope; target count check skipped"
        : > /tmp/.kitchen-tgt.$$
    fi
    rm -f /tmp/.kitchen-tgt-f.$$
    # Read in the MAIN shell, for the reason the two rules above both record: a
    # `... | while read` runs its body in a subshell and fail()'s _FAILED=1 dies with it.
    while IFS= read -r h; do
        [ -n "$h" ] || continue
        _f=${h%%:*}; _f=${_f#"$REPO_ROOT"/}; _rest=${h#*:}
        _ln=${_rest%%:*}
        _said=$(printf '%s' "${_rest#*:}" | sed 's/[[:space:]-]*[Tt]argets\{0,1\}$//' \
                | tr 'A-Z' 'a-z')
        [ "$_said" = "$want" ] || [ "$_said" = "$n" ] || \
            fail "$_f:$_ln: says '$_said targets', but there are $n (want '$want')"
    done < /tmp/.kitchen-tgt.$$
    rm -f /tmp/.kitchen-tgt.$$
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
