#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Trailing whitespace, missing final newline, CRLF, tabs-vs-spaces in YAML.
#
# Note: CRLF is ALLOWED in ci/fixtures and in anything mirroring upstream's
# bootinst.bat / *.vbs, which are genuinely CRLF files.
. "$(dirname "$0")/../lib.sh"

CR=$(printf '\r')
TAB=$(printf '\t')

check_files_nl | while IFS= read -r f; do
    case "$f" in vendor/*|*.png|*.jpg|*.gz|*.xz|*.bat|*.vbs) continue ;; esac
    [ -f "$REPO_ROOT/$f" ] || continue
    case "$(file -b --mime-encoding "$REPO_ROOT/$f")" in binary) continue ;; esac

    file_content "$f" | grep -nq '[[:space:]]$'        && echo "TRAIL $f"
    file_content "$f" | grep -q "$CR"                  && echo "CRLF $f"
    [ -n "$(file_content "$f" | tail -c1)" ]           && echo "NOEOL $f"
    case "$f" in *.yaml|*.yml)
        file_content "$f" | grep -nq "^$TAB"            && echo "YAMLTAB $f" ;;
    esac
done > /tmp/.kitchen-ws.$$ 2>/dev/null

while IFS= read -r line; do
    case "$line" in
        TRAIL\ *)   fail "trailing whitespace: ${line#TRAIL }" ;;
        CRLF\ *)    fail "CRLF line endings: ${line#CRLF }" ;;
        NOEOL\ *)   fail "no newline at end of file: ${line#NOEOL }" ;;
        YAMLTAB\ *) fail "tab indentation in YAML: ${line#YAMLTAB }" ;;
    esac
done < /tmp/.kitchen-ws.$$
rm -f /tmp/.kitchen-ws.$$
check_result
