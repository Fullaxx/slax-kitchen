#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Reject *.DNC.md working files and a personal boot-host.ini.
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

# boot-host.ini, for the same reason with a sharper edge. It names a machine, an account's
# scratch directory and a VNC address, all of them personal -- and it is not merely
# embarrassing in a clone, it is ACTIVE: while that file exists, every `kitchen test` in
# the tree sends its ISO to the host it names and boots it there. A fork that committed
# one would point every cloner's boot tests at the fork author's machine, and the clone
# would work, so nobody would look.
#
# The .example.ini template is the committed one and must keep passing; it is matched out
# by anchoring on the exact name rather than on a substring.
check_files_nl | grep -E '(^|/)boot-host\.ini$' > /tmp/.kitchen-bh.$$ || true
while IFS= read -r f; do
    [ -n "$f" ] || continue
    fail "$f must not be committed: it names YOUR boot host, and a clone would send its
      boot tests there.  git rm --cached $f   (gitignored by design; the template
      committed here is boot-host.example.ini)"
done < /tmp/.kitchen-bh.$$
rm -f /tmp/.kitchen-bh.$$

# Also reject *pointers* to them from anything a user actually receives. The files are
# gitignored by design, so "see HOST_TASKS.DNC.md" in an error message or a doc sends the
# reader after something that cannot exist in their clone -- which is exactly what three
# shipped `kitchen` messages and two doc pages did. host-handoff.md is the one page whose
# subject IS these files; it explains them, so it is allowed to name them.
check_files_nl | grep -vE '(^|/)(host-handoff\.md|10-no-dnc\.sh|\.gitignore)$' \
  | grep -E '\.(md|sh|py|ya?ml)$|(^|/)kitchen$' > /tmp/.kitchen-dncref.$$ || true
while IFS= read -r f; do
    [ -n "$f" ] || continue
    [ -f "$f" ] || continue
    if grep -q 'DNC\.md' "$f" 2>/dev/null; then
        fail "$f names a *.DNC.md working file; readers cannot see it (link a committed doc instead)"
    fi
done < /tmp/.kitchen-dncref.$$
rm -f /tmp/.kitchen-dncref.$$
check_result
