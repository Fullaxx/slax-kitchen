#!/bin/sh
# stages: pre-commit pre-push ci
# desc: YAML fenced in markdown must validate against the same schemas recipes do.
#
# Issue #3 shipped because nothing ever ran the documented form: both docs showed
# `sign: "your-key-id"` for iso.checksums while the schema typed it boolean, so the
# documented recipe was a hard validation error and no shipped recipe used the field.
# 40-schema covers recipes/, profiles/, compat/ and schema/ -- docs/ was the one place a
# wrong example could sit forever. On the day it was written this gate found seven more,
# across three verbs, including one the repo had already fixed in exactly one of the four
# files it appeared in.
#
# Whole-tree by design, like 90-doc-coverage and 95-status-vocab: a doc example is wrong
# whether or not this particular commit touched it, and the block most likely to be wrong
# is one that was copied from another page months ago.
#
# The work is one python3 process for every block in the tree (~30 ms). Per-file
# subprocesses, the way 40-schema calls validate.py, would mean ~55 interpreter startups
# plus as many yaml+jsonschema imports -- seconds, on a gate suite that takes ~35.
. "$(dirname "$0")/../lib.sh"

[ -d "$REPO_ROOT/schema" ] || { note "schema/ not present yet - skipping"; exit 0; }
[ -f "$REPO_ROOT/ci/doc-yaml.py" ] || { note "ci/doc-yaml.py not present - skipping"; exit 0; }
have python3 || { note "python3 not installed - skipping doc YAML validation"; exit 0; }
python3 -c 'import yaml, jsonschema' 2>/dev/null || {
    note "python3 yaml/jsonschema not importable - skipping (see kitchen doctor)"
    exit 0
}

python3 "$REPO_ROOT/ci/doc-yaml.py" > /tmp/.kitchen-docyaml.$$ 2> /tmp/.kitchen-docyaml-n.$$
rc=$?

# 2 means the checker could not run. A checker that dies must not look like one that
# passed -- that is the whole "a check that cannot fail is worse than no check" trap.
if [ "$rc" -gt 1 ]; then
    fail "doc-yaml: checker failed to run: $(head -1 /tmp/.kitchen-docyaml-n.$$)"
fi

# Read in the MAIN shell. A `... | while read` pipeline runs its last stage in a subshell,
# so fail() would set _FAILED=1 in a process that then exits -- printing FAIL and
# returning 0. See the same note in 90-doc-coverage.sh.
while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    fail "doc yaml: $(printf '%s' "$hit" | tr '\t' ' ')"
done < /tmp/.kitchen-docyaml.$$

# How many blocks were checked, and how many were skipped as not-our-documents. Printed
# rather than asserted: a count that must be kept up to date is a gate people disable.
# But a silent skip is how coverage rots, so it is at least visible.
note "$(head -1 /tmp/.kitchen-docyaml-n.$$)"

rm -f /tmp/.kitchen-docyaml.$$ /tmp/.kitchen-docyaml-n.$$
check_result
