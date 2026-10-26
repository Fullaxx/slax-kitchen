#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Each submodule .gitmodules names is committed as a submodule pointer, never as files.
#
# WHAT IT CHECKS. For each path the project's own .gitmodules names, the index records a
# submodule pointer there: a gitlink, mode 160000. Git keeps no file under a path that holds
# a pointer, so this one rule also refuses a vendored tree committed as files, and names
# them. .gitmodules and the pointers are both read from the index, in either scope, because
# that is what a commit records, and under a hook it is the index the hook names. The paths
# are read, never written here, so the same file serves every project that copies it:
# vendor/linux-live here, vendor/slax-kitchen in slax-wine and slax-rpgs (#103).
#
# WHY. A commit can carry another project's code into this history in two ways only: as
# files under its path, or as a pointer to a commit that project never made. History is
# permanent, so the first is refused here. The second is left to review, where a commit that
# moves a pointer shows `Subproject commit`, and to the note in
# docs/15-upstream/drift-report.md that a move needs, which no gate checks. Here the
# vendored tree is Linux Live Kit, GPL inside an MIT repository and vendored unmodified
# (NOTICE.md). Downstream it is this engine.
#
# WHERE IT CAME FROM. 79ebce1 (2026-09-13) wrote it to keep "the MIT/GPLv2 boundary from
# eroding". Until #103 it also read the submodule's working tree: that it was checked out at
# its pin, and (#101) that nothing inside it was edited or untracked. Measured 2026-10-26,
# the first failed a commit that left a moved checkout unstaged, which would have moved
# nothing, and passed `git add -A` and `git commit -a`, which staged the move. A working
# tree never reaches history, because a pointer records a commit, and in CI, where every
# checkout is fresh, neither rule could fail. `git status` shows a moved or edited
# submodule as ` M <path>`. Downstream, gate 96 fails a moved pointer, and each image's
# provenance record marks an edited engine dirty.
#
# NOT CHECKED: whether a newly recorded pointer is a commit the vendored project made.
. "$(dirname "$0")/../lib.sh"

# EXIT 1 FROM `git config --blob --get-regexp` MEANS THREE THINGS: no such blob, no such key,
# and a blob git cannot parse. Measured 2026-10-26 with git 2.43: a broken line printed
# "error: bad config line 2 in blob :.gitmodules" and exited 1, and this gate's first draft
# passed that .gitmodules as one naming nothing. The blob is asked for first, then read whole
# with --list, which exits 128 on that line and 0 on an empty file. Only then is 1 "no key".
if ! git -C "$REPO_ROOT" cat-file -e :.gitmodules 2>/dev/null; then
    note "no .gitmodules in the index - no submodules to check"
    exit 0
fi
if ! _err=$(git -C "$REPO_ROOT" config --blob :.gitmodules --list 2>&1 >/dev/null); then
    fail "git cannot read .gitmodules: $(printf '%s\n' "$_err" | head -1)"
    check_result; exit
fi
_keys=$(git -C "$REPO_ROOT" config --blob :.gitmodules --name-only \
        --get-regexp '^submodule\..*\.path$' 2>&1)
case $? in
    0) ;;
    1) note ".gitmodules names no submodule path - nothing to check"; exit 0 ;;
    *) fail "git cannot read .gitmodules: $(printf '%s\n' "$_keys" | head -1)"
       check_result; exit ;;
esac

# Key by key, because a submodule's name may hold a space, and `--get-regexp` separates a
# key from its value with one.
while IFS= read -r _key; do
    [ -n "$_key" ] || continue
    _path=$(git -C "$REPO_ROOT" config --blob :.gitmodules --get "$_key")
    if [ -z "$_path" ]; then
        fail ".gitmodules: $_key is empty"
        continue
    fi
    # Literal, because a path is not a pattern. -z, because without it git quotes a path
    # holding a byte above 127, as check_files() in ci/lib.sh knows: measured 2026-10-26,
    # deps/ünï came back as "deps/\303\274n\303\257" and its pointer read as missing. The
    # path reaches awk through ENVIRON, because `awk -v` reads a backslash in it as an
    # escape (measured with mawk). The mode is read as ci/lib.sh's file_size reads it.
    _entries=$(git -C "$REPO_ROOT" --literal-pathspecs ls-files -s -z -- "$_path" 2>/dev/null \
               | tr '\0' '\n')
    _mode=$(printf '%s\n' "$_entries" | P="$_path" awk -F '\t' \
            '$2 == ENVIRON["P"] { split($1, f, " "); print f[1] }')
    [ "$_mode" = 160000 ] && continue
    _files=$(printf '%s\n' "$_entries" | P="$_path/" awk -F '\t' \
             'index($2, ENVIRON["P"]) == 1 { print $2 }')
    if [ -n "$_files" ]; then
        _n=$(printf '%s\n' "$_files" | wc -l | tr -d ' ')
        fail "$_path is a submodule in .gitmodules, and is committed as $_n file(s) instead of a pointer: $(printf '%s\n' "$_files" | head -3 | tr '\n' ' ')"
    else
        fail "$_path is a submodule in .gitmodules, but the index records no pointer there"
    fi
    note "see: git submodule status -- $_path"
done <<EOF
$_keys
EOF
check_result
