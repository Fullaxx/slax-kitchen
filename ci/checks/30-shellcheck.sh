#!/bin/sh
# stages: pre-commit pre-push ci
# desc: shellcheck every shell script.
. "$(dirname "$0")/../lib.sh"

have shellcheck || { warn "shellcheck not installed - skipping (install: apt-get install shellcheck)"; exit 0; }

is_shell() {
    case "$1" in
        *.sh) return 0 ;;
        kitchen|ci/hooks/*) return 0 ;;
    esac
    # vendored upstream is not ours to lint
    case "$1" in vendor/*) return 1 ;; esac
    # shebang sniff for extensionless scripts
    head -c 40 "$REPO_ROOT/$1" 2>/dev/null | head -1 | grep -qE '^#!.*\b(sh|bash|dash)\b' && return 0
    return 1
}

check_files_nl | while IFS= read -r f; do
    case "$f" in vendor/*) continue ;; esac
    [ -f "$REPO_ROOT/$f" ] || continue
    is_shell "$f" && echo "$f"
done > /tmp/.kitchen-sh.$$

if [ -s /tmp/.kitchen-sh.$$ ]; then
    # -x follows `source`d files; -e informational keeps noise down without hiding real bugs
    while IFS= read -r f; do
        shellcheck -x -S warning -e SC1091 "$REPO_ROOT/$f" || fail "shellcheck: $f"
    done < /tmp/.kitchen-sh.$$
fi
rm -f /tmp/.kitchen-sh.$$
check_result
