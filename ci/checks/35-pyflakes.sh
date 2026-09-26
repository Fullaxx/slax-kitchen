#!/bin/sh
# stages: pre-commit pre-push ci
# desc: pyflakes every python file.
#
# Beside 30-shellcheck, and for the same reason: every shell script in this tree is
# linted and no python file was. The engine is python -- lib/apply.py alone is 4000 lines
# -- so the half that was unchecked was the larger half.
#
# WHAT PUT IT HERE. 18294f5 added `import traceback` to 23 test files so one crashing test
# could not take the rest of the file with it, and in three of them the line landed inside
# a triple-quoted stub script. The guard it was adding then raised NameError instead of
# reporting the crash, and skipped every remaining test in the file (#37). pyflakes names
# all three in under a second; nothing else in the tree could say a word about it.
#
# It found a shipped defect on the first run too: bundle.script worked out which files a
# step had removed and never printed them, where bundle.packages does (#38). That is what
# an unused local can mean.
#
# WHY NOT flake8 OR ruff. Neither is installed here and both bring style rules this tree
# has no opinion on. pyflakes reports what is wrong rather than what is unfashionable:
# undefined names, unused imports and locals, shadowed definitions.
#
# NO INLINE SUPPRESSION, deliberately and unavoidably -- pyflakes ignores `# noqa`. An
# "unused" import that is load-bearing is a real problem to fix rather than silence:
# tools/firmware-coverage.py read as having an unused `import urllib.request` because a
# function-local `import urllib.parse` shadowed the module binding, while lines further
# down depended on the module-level import's side effect. Deleting it would have broken
# the tool; hoisting the local import fixed both the shadowing and the report.
#
# THE MODULE, NOT A BINARY ON PATH, and that distinction broke CI the day this gate
# landed. `kitchen doctor --strict` was given a TOOLS row asking `have pyflakes`, which
# passed on the machine it was written on because pyflakes was there from a pip venv.
# In the reference container apt installs python3-pyflakes, which on ubuntu:24.04 ships
# the MODULE ONLY -- the binary is a separate package, pyflakes3 -- so doctor reported
# `MISS pyflakes (apt-get install python3-pyflakes)` about a package that was already
# installed, and both container jobs failed. doctor_toolchain in kitchen already stated
# the rule for python3-yaml and python3-jsonschema: "Import them, the same way
# lib/validate.py does, rather than asking dpkg." `python3 -m pyflakes` is the same answer
# for the same reason, and it works whether the tool arrived from apt or from a venv.
. "$(dirname "$0")/../lib.sh"

python3 -c "import pyflakes" 2>/dev/null \
    || { warn "python3 cannot import pyflakes - skipping (install: apt-get install python3-pyflakes)"; exit 0; }

check_files_nl | while IFS= read -r f; do
    case "$f" in
        vendor/*) continue ;;          # vendored upstream is not ours to lint
        *.py) ;;
        *) continue ;;
    esac
    [ -f "$REPO_ROOT/$f" ] || continue
    echo "$REPO_ROOT/$f"
done > "${TMPDIR:-/tmp}/.kitchen-py.$$"

# One invocation, not one per file: 55 files cost 0.65 s together and python's startup
# dominates a per-file loop. Measured 2026-09-22.
if [ -s "${TMPDIR:-/tmp}/.kitchen-py.$$" ]; then
    # shellcheck disable=SC2046  # the list is paths this gate just wrote, one per line
    python3 -m pyflakes $(cat "${TMPDIR:-/tmp}/.kitchen-py.$$") || fail "pyflakes"
fi
rm -f "${TMPDIR:-/tmp}/.kitchen-py.$$"
check_result
