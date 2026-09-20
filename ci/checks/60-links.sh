#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Internal markdown links must resolve, fragment included (external URLs are not fetched).
#
# The docs tree is heavily cross-linked -- 122 markdown files on 2026-09-20 -- and broken
# relative links rot fast and are invisible until someone follows one. This said ~60 until
# then, while line 41 of this same file said 122: one number, one file, two answers.
#
# AND FOR A YEAR THIS GATE CHECKED ONLY THE HALF BEFORE THE `#`. The extractor was
# `grep -oE '\]\([^)#[:space:]]+'`, and the `#` in that negated class stopped every target
# at its fragment: `[x](foo.md#bar)` had `foo.md` checked and `#bar` thrown away, and
# `[x](#bar)` matched nothing at all and was invisible here. So a heading rename broke
# every link to it in silence. Five were broken when this was written, all naming the
# `sources` heading in docs/90-reference/cli.md, which 9907ed3 -- a self-review pass --
# grew a `[--strict]` on. The slug moved; NOTICE.md, publishing-images.md,
# reproducibility.md, ci.md and verbs.md went on pointing at where it used to be.
#
# WHOLE-TREE BY DESIGN, like 45-doc-yaml and 95-status-vocab, and here the scope IS the
# point: 9907ed3 touched cli.md and none of the five files that link to it, so a gate
# reading only the staged files would have passed the commit that broke them.
#
# THE GAP THAT LEAVES, written down rather than met later: the file list comes from the
# index, but the content is read from DISK, so during a partial commit this judges the
# working tree rather than what is about to be committed, and a file that is not tracked at
# all is not listed at any stage. 45-doc-yaml makes the same trade for the same reason -- an
# anchor is broken, or it is not, independently of which commit happens to be in flight.
#
# REWRITTEN IN PYTHON RATHER THAN EXTENDED, because the fragment half needs a parser. 51
# lines inside fenced code blocks in this tree satisfy the ATX heading rule, four of them
# in untagged fences, so a grep for `^#` invents 51 anchors that do not exist -- the same
# argument ee69f5d made one commit earlier about a check that prose could switch off.
# ci/md-links.py says what the slug rules are and which of them this tree exercises.
#
# The lesson the old extractor carried is kept because it is still true of anything that
# reads links: `grep -o`, not `sed -n s///p`, because a substitution yields at most ONE
# match per line, so a line carrying two links only ever got one of them checked. The
# rewrite gets this for free -- finditer over the document, which also catches the one
# link in this tree whose text wraps across two source lines.
#
# The work is one python3 process over every markdown file in the tree: 86 ms, measured
# 2026-09-20, median of five, on a gate suite that takes ~35 s. Per-file subprocesses would
# mean 122 interpreter startups for the same answer, and the anchors have to be collected
# tree-wide before any link can be resolved anyway.
. "$(dirname "$0")/../lib.sh"

# WHY THIS REFUSES RATHER THAN STANDS DOWN is ci/lib.sh's require_python3, stated once
# there. What is specific to this gate is the size of the hole: nothing else in this tree
# looks at a link, so there is no second gate to degrade to. And before the rewrite this one
# was sh and grep and wanted no python3 at all -- standing down would have turned a gate that
# worked on such a machine into one that prints ok having read nothing, as a side effect of a
# refactor.
require_python3 "no internal link is checked at all"
[ -f "$REPO_ROOT/ci/md-links.py" ] || { fail "ci/md-links.py is missing"; check_result; exit; }

python3 "$REPO_ROOT/ci/md-links.py" > /tmp/.kitchen-links.$$ 2> /tmp/.kitchen-links-n.$$
rc=$?

# 2 means the checker could not run. A checker that dies must not look like one that
# passed -- the same rule ci/lib.sh states for a git that will not answer.
if [ "$rc" -gt 1 ]; then
    fail "md-links: checker failed to run: $(head -1 /tmp/.kitchen-links-n.$$)"
fi

# Read in the MAIN shell. A `... | while read` pipeline runs its last stage in a subshell,
# so fail() would set _FAILED=1 in a process that then exits -- printing FAIL and
# returning 0. See the same note in 45-doc-yaml.sh and 90-doc-coverage.sh.
while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    fail "$(printf '%s' "$hit" | tr '\t' ' ')"
done < /tmp/.kitchen-links.$$

# Printed rather than asserted, for the reason 45-doc-yaml gives: a count that must be kept
# up to date is a gate people disable, but a silent skip is how coverage rots.
note "$(head -1 /tmp/.kitchen-links-n.$$)"

rm -f /tmp/.kitchen-links.$$ /tmp/.kitchen-links-n.$$
check_result
