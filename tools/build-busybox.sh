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
# WHY A CONTAINER. This host has no 32-bit libc and no musl. i386/alpine has both, needs
# no privilege, and pins the toolchain as firmly as the source. Nothing is bind-mounted --
# the daemon may not share this filesystem -- so the script goes in on stdin and the
# finished binary comes out on stdout.
#
# WHY NOT A CHECKED-IN .config. A 1000-line .config pins every symbol and says nothing
# about intent, and has to be regenerated wholesale for every version bump. Starting from
# the tarball's own `defconfig` and flipping a named, commented list states exactly what
# we need that upstream's default does not give us, and survives a version bump.
set -u

VERSION=1.37.0
OUTPUT=""
CHECK_PARITY=0
IMAGE=i386/alpine:3.19

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

# The whole build, run inside the container. Diagnostics to stderr, binary to stdout.
docker run --rm -i "$IMAGE" sh -s "$VERSION" "$SHA" > "$OUTPUT.part" <<'CONTAINER'
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
  `# livekitlib calls \`date --date "$1" '+%s'\`, which is a long option.` \
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

if ! make -j"$(nproc)" >/tmp/build.log 2>&1; then
    echo "=== build failed ==="
    grep -nE "error|Error|undefined reference|No rule" /tmp/build.log | head -25
    exit 1
fi
echo "built $(wc -c < busybox) bytes"
cat busybox >&3
CONTAINER

rc=$?
if [ "$rc" != 0 ] || [ ! -s "$OUTPUT.part" ]; then
    rm -f "$OUTPUT.part"
    echo "build-busybox: build failed (exit $rc)" >&2
    exit 1
fi
mv "$OUTPUT.part" "$OUTPUT"
chmod +x "$OUTPUT"

# Assert what the initramfs actually requires, rather than trusting the toolchain.
desc=$(file -b "$OUTPUT" 2>/dev/null || echo "")
case "$desc" in
    *"ELF 32-bit"*"Intel 80386"*"statically linked"*) ;;
    *) echo "build-busybox: wrong binary type -- $desc" >&2; exit 1 ;;
esac
printf '  %s\n' "$desc" >&2
printf '  %s\n' "$("$OUTPUT" 2>&1 | head -1)" >&2

if [ "$CHECK_PARITY" = 1 ]; then
    # Running the binary needs IA32 emulation on THIS host. If it is absent the applet
    # list cannot be read here -- which is also exactly the constraint the target kernel
    # has, so it is worth finding out now.
    if ! "$OUTPUT" --list >/dev/null 2>&1; then
        echo "build-busybox: cannot execute the i386 binary on this host." >&2
        echo "  Parity cannot be checked here. The same limitation applies to any" >&2
        echo "  kernel without CONFIG_IA32_EMULATION -- see the cookbook page." >&2
        exit 1
    fi
    printf '  %s applets\n' "$("$OUTPUT" --list | wc -l)" >&2
fi

echo "$OUTPUT"
