#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Reject *.DNC.md working files.
#
# INITIAL_PLAN.DNC.md / INITIAL_TASKS.DNC.md / HOST_TASKS.DNC.md are session working
# state, deliberately outside project history. Gitignored, and rejected here too so a
# `git add -f` or a wildcard add cannot slip one in.
. "$(dirname "$0")/../lib.sh"

check_files_nl | grep -i '\.DNC\.md$' | while IFS= read -r f; do
    echo "$f"
done > /tmp/.kitchen-dnc.$$
while IFS= read -r f; do
    [ -n "$f" ] && fail "working file must not be committed: $f  (gitignored by design)"
done < /tmp/.kitchen-dnc.$$
rm -f /tmp/.kitchen-dnc.$$
check_result
