#!/bin/sh
# Build a static i386 busybox suitable for the Slax initramfs.
#
#   tools/build-busybox.sh [-o OUTPUT] [-v VERSION] [--check-parity]
#
# WHY A SCRIPT AND NOT A RECIPE. Recipes are declarative YAML applied to an unpacked
# tree; nothing in that vocabulary compiles anything, and it should stay that way. So the
# binary is built here, once, and the `initramfs-busybox` recipe installs the artifact.
#
# WHY i386. bin/busybox is byte-identical across all four shipped ISOs, and so is every
# other file in the initramfs except lib/modules/<ver>/. It is a 32-bit binary even on the
# 64-bit images, which is why those images need CONFIG_IA32_EMULATION in any replacement
# kernel. One i386 build therefore serves all four targets.
#
# WHY A CONTAINER. This host has no 32-bit libc and no musl. i386/alpine has both and needs
# no privilege. Nothing is bind-mounted -- the daemon may not share this filesystem -- so the
# script goes in on stdin and the results come out on stdout, as a tar.
#
# WHAT IS PINNED, AND WHAT IS ONLY RECORDED. The busybox tarball is pinned by sha256 and the
# image by digest. The apk packages the build installs are NOT pinned -- Alpine's stable
# branch takes security updates -- so their exact versions are recorded instead, in the
# build claim written beside the binary (<output>.provenance.json), together with the
# resulting .config. An earlier version of this comment said the container "pins the
# toolchain as firmly as the source" while pulling a floating tag and unversioned packages,
# and kept neither the config nor the versions: nothing could have checked it.
#
# WHY NOT A CHECKED-IN .config. A 1000-line .config pins every symbol and says nothing
# about intent, and has to be regenerated wholesale for every version bump. Starting from
# the tarball's own `defconfig` and flipping a named, commented list states exactly what
# we need that upstream's default does not give us, and survives a version bump.
set -u

VERSION=1.37.0
OUTPUT=""
CHECK_PARITY=0
# 3.19.9, pinned 2026-09-17. Bumping it is deliberate: pull, read the digest, change both.
IMAGE=i386/alpine:3.19@sha256:394df6ab0b40dfb37a7581f3351db25fcc7fa30e50ac4b1249e1de22f2a47692

# Pinned by upstream's own published .sha256 file, checked inside the container before a
# single line is compiled.
sha_for() {
    case "$1" in
        1.37.0) echo 3311dff32e746499f4df0d5df04d7eb396382d7e108bb9250e7b519b837043a4 ;;
        1.36.1) echo b8cc24c9574d809e7279c3be349795c5d5ceb6fdf19ca709f80cde50e47de314 ;;
        *)      echo "" ;;
    esac
}

while [ $# -gt 0 ]; do
    case "$1" in
        -o|--output)  OUTPUT=$2; shift 2 ;;
        -v|--version) VERSION=$2; shift 2 ;;
        --check-parity) CHECK_PARITY=1; shift ;;
        -h|--help)
            sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

SHA=$(sha_for "$VERSION")
[ -n "$SHA" ] || {
    echo "build-busybox: no pinned sha256 for $VERSION." >&2
    echo "  Add one to sha_for() from https://busybox.net/downloads/busybox-$VERSION.tar.bz2.sha256" >&2
    echo "  An unpinned download is not an acceptable source for code that runs as root at boot." >&2
    exit 2
}
[ -n "$OUTPUT" ] || OUTPUT="build/busybox-$VERSION-i386-static"

command -v docker >/dev/null || {
    echo "build-busybox: docker not found." >&2
    echo "  This needs an i386 userland with musl, which this host does not have." >&2
    exit 2
}

mkdir -p "$(dirname "$OUTPUT")"
echo "building busybox $VERSION (static, i386, musl) in $IMAGE" >&2

# The whole build, run inside the container. Diagnostics to stderr; on stdout, a tar of
# the binary, its .config, and the facts the build claim records.
docker run --rm -i "$IMAGE" sh -s "$VERSION" "$SHA" > "$OUTPUT.tar.part" <<'CONTAINER'
set -e
VER=$1
SHA=$2
exec 3>&1 1>&2          # stdout becomes stderr; fd 3 is the real stdout

# linux-headers is NOT optional and its absence is not obvious: musl-dev alone lacks
# linux/kd.h, and the build dies in console-tools/kbd_mode.c a couple of minutes in.
apk add --no-cache gcc musl-dev linux-headers make perl bzip2 tar wget >/dev/null 2>&1

cd /tmp
wget -q "https://busybox.net/downloads/busybox-$VER.tar.bz2"
echo "$SHA  busybox-$VER.tar.bz2" | sha256sum -c - >/dev/null
echo "tarball sha256 verified"
tar xjf "busybox-$VER.tar.bz2"
cd "busybox-$VER"

make defconfig >/dev/null 2>&1

# Every deviation from defconfig, and why. Keep this list short and explained.
sed -i \
  `# The initramfs carries no dynamic loader, so nothing else is even an option.` \
  -e 's/^# CONFIG_STATIC is not set/CONFIG_STATIC=y/' \
  -e 's/^CONFIG_FEATURE_SHARED_BUSYBOX=y/# CONFIG_FEATURE_SHARED_BUSYBOX is not set/' \
  `# livekitlib calls \`date --date "$1" '+%s'\`, which is a long option. Already y in` \
  `# defconfig, so this is a guard against a future default rather than a change.` \
  -e 's/^# CONFIG_LONG_OPTS is not set/CONFIG_LONG_OPTS=y/' \
  `# MODPROBE_SMALL is a reduced implementation with different alias and blacklist` \
  `# handling, and modprobe_everything() fires it hundreds of times per boot. Alpine` \
  `# ships the full modutils for the same reason.` \
  -e 's/^CONFIG_MODPROBE_SMALL=y/# CONFIG_MODPROBE_SMALL is not set/' \
  `# Applet parity with the shipped 1.26.2: these three are still in 1.37.0 but are` \
  `# default n. Cheap, and they remove any question about what was lost.` \
  -e 's/^# CONFIG_AR is not set/CONFIG_AR=y/' \
  -e 's/^# CONFIG_UNLZOP is not set/CONFIG_UNLZOP=y/' \
  -e 's/^# CONFIG_LZOPCAT is not set/CONFIG_LZOPCAT=y/' \
  .config
yes '' | make oldconfig >/dev/null 2>&1

# ASSERT the outcome rather than hoping the seds matched. A sed that does not match is
# silent, and one of these four turned out to be a no-op for weeks because defconfig had
# already set it -- which is fine, but "fine" should be something the build knows rather
# than something nobody checked. oldconfig can also flip a symbol back if a dependency
# it needs is off.
for want in CONFIG_STATIC=y CONFIG_LONG_OPTS=y CONFIG_AR=y CONFIG_UNLZOP=y CONFIG_LZOPCAT=y; do
    grep -qx "$want" .config || { echo "config assertion failed: want $want, got:"; \
        grep -E "^(# )?${want%%=*}( |=)" .config || echo "  (symbol absent entirely)"; exit 1; }
done
grep -qx '# CONFIG_MODPROBE_SMALL is not set' .config || {
    echo "config assertion failed: CONFIG_MODPROBE_SMALL should be off"; exit 1; }
grep -qx '# CONFIG_FEATURE_SHARED_BUSYBOX is not set' .config || {
    echo "config assertion failed: CONFIG_FEATURE_SHARED_BUSYBOX should be off"; exit 1; }
echo "config assertions passed"

if ! make -j"$(nproc)" >/tmp/build.log 2>&1; then
    echo "=== build failed ==="
    grep -nE "error|Error|undefined reference|No rule" /tmp/build.log | head -25
    exit 1
fi
echo "built $(wc -c < busybox) bytes"
mkdir -p /tmp/out
cp busybox .config /tmp/out/
cat /etc/alpine-release > /tmp/out/alpine-release
apk info -v 2>/dev/null | sort > /tmp/out/apk-versions
tar -C /tmp/out -cf - busybox .config alpine-release apk-versions >&3
CONTAINER

rc=$?
if [ "$rc" != 0 ] || [ ! -s "$OUTPUT.tar.part" ]; then
    rm -f "$OUTPUT.tar.part"
    echo "build-busybox: build failed (exit $rc)" >&2
    exit 1
fi
UNPACK=$(mktemp -d)
tar -C "$UNPACK" -xf "$OUTPUT.tar.part" && rm -f "$OUTPUT.tar.part"
[ -s "$UNPACK/busybox" ] || { echo "build-busybox: no binary in the container's output" >&2; exit 1; }
# `set -e` is deliberately NOT on (the container run's exit code is read by hand below),
# so every step that the final "here is your binary" line depends on is checked here.
mv "$UNPACK/busybox" "$OUTPUT" && mv "$UNPACK/.config" "$OUTPUT.config" && chmod +x "$OUTPUT" || {
    echo "build-busybox: could not put the build output at $OUTPUT" >&2
    rm -rf "$UNPACK"
    exit 1
}

# THE BUILD CLAIM. `initramfs.busybox` records it in the image's provenance, and `kitchen
# sources` uses it to name what this binary was built from. `artifact.sha256` is what ties
# the claim to THIS binary; a claim whose hash does not match is reported as unverified.
if ! python3 - "$OUTPUT" "$VERSION" "$SHA" "$IMAGE" "$UNPACK" "$(git -C "$(dirname "$0")/.." \
        hash-object tools/build-busybox.sh 2>/dev/null || echo)" <<'PY'
import hashlib, json, os, sys
out, ver, sha, image, unpack, blob = sys.argv[1:7]
def h(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()
apk = {}
for ln in open(os.path.join(unpack, "apk-versions")).read().split():
    name, _, rest = ln.rpartition("-")
    name, _, ver2 = name.rpartition("-")
    apk[name] = f"{ver2}-{rest}"
claim = {
    "schema": "slax-kitchen/build-claim/v1",
    "artifact": {"name": os.path.basename(out), "sha256": h(out), "arch": "i386", "static": True},
    "script": {"path": "tools/build-busybox.sh", "git_blob": blob or None},
    "container": {"image": image,
                  "alpine_release": open(os.path.join(unpack, "alpine-release")).read().strip(),
                  "apk": apk},
    "sources": [{"name": "busybox", "version": ver, "license": "GPL-2.0-only",
                 "url": f"https://busybox.net/downloads/busybox-{ver}.tar.bz2", "sha256": sha}],
    "config": {"file": os.path.basename(out) + ".config", "sha256": h(out + ".config")},
    "linked": [{"name": "musl", "version": apk.get("musl-dev") or apk.get("musl"),
                "license": "MIT"}],
}
with open(out + ".provenance.json", "w") as f:
    json.dump(claim, f, indent=1, sort_keys=True)
    f.write("\n")
PY
then :; else
    # Without the claim, `kitchen sources` reports this binary as built here with nothing
    # to show for it, and the initramfs it goes into is unresolved. That is a failed
    # build, not a warning -- and the half-written file must not be left to be believed.
    echo "build-busybox: the build claim could not be written (python3 exited $?)" >&2
    rm -f "$OUTPUT.provenance.json"
    rm -rf "$UNPACK"
    exit 1
fi
rm -rf "$UNPACK"

# Assert what the initramfs actually requires, rather than trusting the toolchain.
desc=$(file -b "$OUTPUT" 2>/dev/null || echo "")
case "$desc" in
    *"ELF 32-bit"*"Intel 80386"*"statically linked"*) ;;
    *) echo "build-busybox: wrong binary type -- $desc" >&2; exit 1 ;;
esac
printf '  %s\n' "$desc" >&2

# busybox dispatches on argv[0]. Invoked as `busybox-1.37.0-i386-static` it hunts for an
# applet of that name and answers "applet not found" -- so the identity line and --list
# below have to go through a symlink actually called busybox.
LINKDIR=$(mktemp -d)
ln -sf "$(cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")" "$LINKDIR/busybox"
trap 'rm -rf "$LINKDIR"' EXIT
printf '  %s\n' "$("$LINKDIR/busybox" 2>&1 | head -1)" >&2

if [ "$CHECK_PARITY" = 1 ]; then
    # Running the binary needs IA32 emulation on THIS host. If it is absent the applet
    # list cannot be read here -- which is also exactly the constraint the target kernel
    # has, so it is worth finding out now.
    if ! "$LINKDIR/busybox" --list >/dev/null 2>&1; then
        echo "build-busybox: cannot execute the i386 binary on this host." >&2
        echo "  Parity cannot be checked here. The same limitation applies to any" >&2
        echo "  kernel without CONFIG_IA32_EMULATION -- see the cookbook page." >&2
        exit 1
    fi
    printf '  %s applets\n' "$("$LINKDIR/busybox" --list | wc -l)" >&2
fi

echo "$OUTPUT"
echo "$OUTPUT.config" >&2
echo "$OUTPUT.provenance.json" >&2
