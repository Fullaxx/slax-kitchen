#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Unit tests for the recipe engine's pure logic (no ISO needed, milliseconds).
. "$(dirname "$0")/../lib.sh"

for t in "$REPO_ROOT"/tests/unit/test_*.py; do
    [ -f "$t" ] || continue
    python3 "$t" >/dev/null 2>/tmp/.kitchen-unit.$$ || {
        fail "$(basename "$t")"
        sed 's/^/      /' /tmp/.kitchen-unit.$$ >&2
    }
    rm -f /tmp/.kitchen-unit.$$
done
check_result
