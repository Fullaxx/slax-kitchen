#!/bin/sh
# Apply every compatible recipe INDIVIDUALLY against one base ISO.
#
#   ci/recipe-matrix.sh <target> <iso> [recipe-dir]
#
# recipe-dir defaults to recipes/available. A fork keeping its own recipes under
# recipes/<project>/ points this at them and gets the same four-target coverage:
#   ci/recipe-matrix.sh debian-64bit-12.2.0 isos/....iso recipes/myproject
#
# One recipe per work tree on purpose: a failure then names exactly one recipe, and
# recipes cannot mask each other. This is what catches a recipe that silently only
# works on 64-bit Debian.
set -u
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET=${1:?usage: recipe-matrix.sh <target> <iso> [recipe-dir]}
ISO=${2:?usage: recipe-matrix.sh <target> <iso> [recipe-dir]}
RECIPE_DIR=${3:-recipes/available}
case "$RECIPE_DIR" in /*) ;; *) RECIPE_DIR="$REPO_ROOT/$RECIPE_DIR" ;; esac
[ -d "$RECIPE_DIR" ] || { echo "no such recipe directory: $RECIPE_DIR" >&2; exit 2; }

FLAVOUR=${TARGET%%-*}
case "$TARGET" in *-32bit-*) ARCH=32bit ;; *) ARCH=64bit ;; esac

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    G=$(printf '\033[32m'); R=$(printf '\033[31m'); Y=$(printf '\033[33m')
    D=$(printf '\033[2m');  O=$(printf '\033[0m')
else G=; R=; Y=; D=; O=; fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-matrix.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

printf 'recipe matrix: %s  (flavour=%s arch=%s)  %s\n' "$TARGET" "$FLAVOUR" "$ARCH" \
       "${RECIPE_DIR#"$REPO_ROOT"/}"
pass=0; fail=0; skip=0

for recipe in "$RECIPE_DIR"/*.yaml; do
    [ -e "$recipe" ] || { echo "no recipes in $RECIPE_DIR" >&2; exit 2; }
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

    if ! $RUN python3 "$REPO_ROOT/lib/apply.py" "$recipe" -w "$tree" >>"$log" 2>&1; then
        printf '  %sFAIL%s %-18s apply failed\n' "$R" "$O" "$name"; tail -12 "$log" | sed 's/^/        /'
        fail=$((fail+1)); rm -rf "$tree"; continue
    fi

    # A bundle that ships var/lib/dpkg/status and outranks a more complete copy makes
    # dpkg forget the difference, with no error anywhere. Check the assembled stack
    # before packing, so the failure names the recipe that built the bundle.
    if ! python3 "$REPO_ROOT/tests/structure/bundle_assert.py" "$tree" >>"$log" 2>&1; then
        printf '  %sFAIL%s %-18s bundle stack does not compose\n' "$R" "$O" "$name"
        grep FAIL "$log" | sed 's/^/        /'
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
    # A recipe may legitimately change the volume id. Read what it asked for from its
    # own pack hints rather than special-casing the recipe name here -- any future
    # recipe that sets volid then gets the right expectation for free.
    _volid=$(sed -n 's/^volid: *//p' "$tree/.kitchen/pack.yaml" 2>/dev/null \
             | head -1 | sed "s/^[\"']//;s/[\"']$//")
    [ -n "$_volid" ] && set -- "$@" --volid "$_volid"
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
