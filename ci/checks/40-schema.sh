#!/bin/sh
# stages: pre-commit pre-push ci
# desc: yamllint + JSON Schema validation of recipes, profiles and sources.
. "$(dirname "$0")/../lib.sh"

have yamllint || warn "yamllint not installed - skipping lint half"
[ -d "$REPO_ROOT/schema" ] || { note "schema/ not present yet - skipping"; exit 0; }
# This gate already refused without python3, but only by accident: lib/validate.py's
# `#!/usr/bin/env python3` cannot exec, so the `|| fail` below fired once per file and said
# "/usr/bin/env: 'python3': No such file or directory" 47 times for one cause. Said once,
# here, before the file list is built -- which also costs the yamllint half on such a
# machine, and that is the trade: the gate is refusing either way.
require_python3 "no recipe, profile or compat file is schema-checked at all"

check_files_nl | grep -E '^(recipes|profiles|compat|schema)/.*\.(ya?ml)$' > /tmp/.kitchen-yaml.$$ || true

if [ -s /tmp/.kitchen-yaml.$$ ]; then
    if have yamllint; then
        while IFS= read -r f; do
            yamllint -f parsable -c "$REPO_ROOT/ci/yamllint.yaml" "$REPO_ROOT/$f" || fail "yamllint: $f"
        done < /tmp/.kitchen-yaml.$$
    fi
    if [ -x "$REPO_ROOT/lib/validate.py" ]; then
        while IFS= read -r f; do
            "$REPO_ROOT/lib/validate.py" "$REPO_ROOT/$f" || fail "schema: $f"
        done < /tmp/.kitchen-yaml.$$
    else
        note "lib/validate.py not present yet - schema half skipped"
    fi
fi
rm -f /tmp/.kitchen-yaml.$$
check_result
