#!/bin/sh
# stages: pre-commit pre-push ci
# desc: vendor/linux-live must stay byte-identical to its pinned upstream commit.
#
# Upstream Linux Live Kit is GPLv2 inside an MIT repo. We vendor it unmodified as a
# submodule and never relicense or patch it in place. Only the submodule POINTER may
# move, and that requires a note in docs/15-upstream/drift-report.md.
. "$(dirname "$0")/../lib.sh"

[ -e "$REPO_ROOT/vendor/linux-live" ] || { note "vendor/linux-live not present yet - skipping"; exit 0; }

# No file inside the submodule tree may be staged directly.
check_files_nl | grep '^vendor/linux-live/' | while IFS= read -r f; do echo "$f"; done \
    > /tmp/.kitchen-vendor.$$
while IFS= read -r f; do
    [ -n "$f" ] && fail "vendor/ must stay pristine, do not commit into it: $f"
done < /tmp/.kitchen-vendor.$$
rm -f /tmp/.kitchen-vendor.$$

# The submodule working tree must be clean (no local edits).
if git -C "$REPO_ROOT" submodule status vendor/linux-live 2>/dev/null | grep -q '^+'; then
    fail "vendor/linux-live has uncommitted local modifications or a moved pointer"
    note "if the pointer move is intentional, record it in docs/15-upstream/drift-report.md"
fi
check_result
