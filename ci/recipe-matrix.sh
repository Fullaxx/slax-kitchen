#!/bin/sh
# Apply every compatible recipe INDIVIDUALLY against one base ISO.
#
#   ci/recipe-matrix.sh <target> <iso>
#
# One recipe per work tree on purpose: a failure then names exactly one recipe, and
# recipes cannot mask each other. This is what catches a recipe that silently only
# works on 64-bit Debian.
set -u
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET=${1:?usage: recipe-matrix.sh <target> <iso>}
ISO=${2:?usage: recipe-matrix.sh <target> <iso>}

FLAVOUR=${TARGET%%-*}
case "$TARGET" in *-32bit-*) ARCH=32bit ;; *) ARCH=64bit ;; esac

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    G=$(printf '\033[32m'); R=$(printf '\033[31m'); Y=$(printf '\033[33m')
    D=$(printf '\033[2m');  O=$(printf '\033[0m')
else G=; R=; Y=; D=; O=; fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-matrix.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

printf 'recipe matrix: %s  (flavour=%s arch=%s)\n' "$TARGET" "$FLAVOUR" "$ARCH"
pass=0; fail=0; skip=0

for recipe in "$REPO_ROOT"/recipes/available/*.yaml; do
    name=$(basename "$recipe" .yaml)

    # Respect the recipe's own compat declaration rather than guessing.
    flav_ok=$(python3 - "$recipe" "$FLAVOUR" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
c = (d.get("compat") or {})
print("yes" if sys.argv[2] in (c.get("flavours") or [sys.argv[2]]) else "no")
PY
)
    arch_ok=$(python3 - "$recipe" "$ARCH" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
c = (d.get("compat") or {})
print("yes" if sys.argv[2] in (c.get("arch") or [sys.argv[2]]) else "no")
PY
)
    if [ "$flav_ok" != yes ] || [ "$arch_ok" != yes ]; then
        printf '  %sskip%s %-18s not declared compatible with %s/%s\n' "$Y" "$O" "$name" "$FLAVOUR" "$ARCH"
        skip=$((skip+1)); continue
    fi

    priv=$(python3 - "$recipe" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
print(((d.get("compat") or {}).get("privilege")) or "none")
PY
)
    tree="$WORK/$name"
    log="$WORK/$name.log"
    rm -rf "$tree"

    if ! "$REPO_ROOT/kitchen" unpack "$ISO" -o "$tree" >"$log" 2>&1; then
        printf '  %sFAIL%s %-18s unpack failed\n' "$R" "$O" "$name"; tail -5 "$log" | sed 's/^/        /'
        fail=$((fail+1)); continue
    fi

    # Any privilege above "none" needs root here. bundle.packages wants a real chroot;
    # the initramfs verbs want CAP_MKNOD, because the archive holds seven device nodes
    # and a non-root cpio silently turns them into empty files. Runners allow sudo; the
    # dev container is already root.
    if [ "$priv" != none ] && [ "$priv" != kvm ] && [ "$(id -u)" != 0 ]; then
        RUN="sudo -E"
    else
        RUN=""
    fi

    if ! $RUN python3 "$REPO_ROOT/lib/apply.py" "$name" -w "$tree" >>"$log" 2>&1; then
        printf '  %sFAIL%s %-18s apply failed\n' "$R" "$O" "$name"; tail -12 "$log" | sed 's/^/        /'
        fail=$((fail+1)); rm -rf "$tree"; continue
    fi

    out="$WORK/$name.iso"
    if ! "$REPO_ROOT/kitchen" pack -s "$tree/iso" -o "$out" >>"$log" 2>&1; then
        printf '  %sFAIL%s %-18s pack failed\n' "$R" "$O" "$name"; tail -12 "$log" | sed 's/^/        /'
        fail=$((fail+1)); rm -rf "$tree"; continue
    fi

    # Assert on what the recipe claims to do, so a no-op recipe is caught.
    set -- "$REPO_ROOT/tests/structure/iso_assert.py" "$out"
    [ "$name" = uefi-bootable ] && set -- "$@" --expect-uefi
    [ "$name" = isohybrid ] && set -- "$@" --expect-hybrid
    if ! python3 "$@" >>"$log" 2>&1; then
        printf '  %sFAIL%s %-18s structure assertions failed\n' "$R" "$O" "$name"
        grep FAIL "$log" | sed 's/^/        /'
        fail=$((fail+1)); rm -rf "$tree" "$out"; continue
    fi

    sz=$(( $(stat -c%s "$out") / 1048576 ))
    printf '  %sok%s   %-18s %s%s MiB%s\n' "$G" "$O" "$name" "$D" "$sz" "$O"
    pass=$((pass+1))
    rm -rf "$tree" "$out"
done

printf '\n%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
