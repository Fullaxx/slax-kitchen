#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Block real credentials (recipes legitimately carry placeholder ones).
#
# Recipes for ssh-server / network-preseed contain example keys and Wi-Fi PSKs by design.
# The rule is therefore "no REAL secrets", enforced by shape, with an explicit allowlist
# marker for deliberate examples:  # kitchen:allow-secret
. "$(dirname "$0")/../lib.sh"

PATTERNS='-----BEGIN (RSA|DSA|EC|OPENSSH|PGP) PRIVATE KEY-----
AKIA[0-9A-Z]{16}
ghp_[A-Za-z0-9]{36}
github_pat_[A-Za-z0-9_]{60,}
xox[baprs]-[A-Za-z0-9-]{10,}
-----BEGIN CERTIFICATE-----[[:space:]]*$'

check_files_nl | while IFS= read -r f; do
    case "$f" in vendor/*|*.png|*.jpg|*.gz|*.xz) continue ;; esac
    [ -f "$REPO_ROOT/$f" ] || continue
    echo "$PATTERNS" | while IFS= read -r pat; do
        [ -n "$pat" ] || continue
        if file_content "$f" | grep -nEq "$pat" 2>/dev/null; then
            file_content "$f" | grep -nE "$pat" | while IFS= read -r hit; do
                ln=${hit%%:*}
                if ! file_content "$f" | sed -n "${ln}p" | grep -q 'kitchen:allow-secret'; then
                    echo "$f:$ln"
                fi
            done
        fi
    done
done > /tmp/.kitchen-sec.$$ 2>/dev/null

while IFS= read -r hit; do
    [ -n "$hit" ] && fail "possible real credential at $hit  (add '# kitchen:allow-secret' if it is a deliberate example)"
done < /tmp/.kitchen-sec.$$
rm -f /tmp/.kitchen-sec.$$
check_result
