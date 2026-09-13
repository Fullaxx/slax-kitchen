#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Internal markdown links must resolve (offline; external URLs are not fetched).
#
# The docs tree is heavily cross-linked across ~60 files; broken relative links rot fast
# and are invisible until someone follows one.
. "$(dirname "$0")/../lib.sh"

check_files_nl | grep -E '\.md$' | grep -v '^vendor/' > /tmp/.kitchen-md.$$ || true

while IFS= read -r f; do
    [ -f "$REPO_ROOT/$f" ] || continue
    dir=$(dirname "$REPO_ROOT/$f")
    # [text](target) where target is relative (no scheme, not a bare anchor)
    sed -nE 's/.*\]\(([^)#[:space:]]+)(#[^)]*)?\).*/\1/p' "$REPO_ROOT/$f" \
      | grep -vE '^(https?|mailto|ftp):' \
      | while IFS= read -r target; do
            [ -n "$target" ] || continue
            case "$target" in /*) cand="$REPO_ROOT$target" ;; *) cand="$dir/$target" ;; esac
            [ -e "$cand" ] || echo "$f -> $target"
        done
done < /tmp/.kitchen-md.$$ > /tmp/.kitchen-links.$$ 2>/dev/null
rm -f /tmp/.kitchen-md.$$

while IFS= read -r hit; do
    [ -n "$hit" ] && fail "broken internal link: $hit"
done < /tmp/.kitchen-links.$$
rm -f /tmp/.kitchen-links.$$
check_result
