#!/bin/sh
# Unpack and repack an ISO with no recipes, then prove the result is still the same ISO.
#
#   ci/roundtrip.sh <iso>
#
# Guards the core claim of the whole toolkit: that a rebuild does not quietly corrupt
# anything. This is a FIDELITY test, not a reproducibility test -- it asserts that every
# file, mode, and boot structure survived, not that the bytes landed in the same place.
#
# The distinction is not academic. genisoimage allocates file extents in directory-scan
# order, and readdir order varies by filesystem: the first CI run rebuilt a structurally
# perfect ISO in which /slax/boot/EFI/Boot/* moved from LBA 212257 to 10877, and a
# sector-by-sector comparison called that 99.9% corrupted. It was not. Comparing layout
# position tests the build host, not the build.
#
# Reproducible byte-identical output is a separate goal with a separate mechanism --
# `kitchen pack --backend xorriso --date ...`. See docs/40-workflow/reproducibility.md.
set -u
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
ISO=${1:?usage: roundtrip.sh <iso>}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-rt.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

echo "round-trip: $(basename "$ISO")"
"$REPO_ROOT/kitchen" unpack "$ISO" -o "$WORK" >/dev/null || exit 1
"$REPO_ROOT/kitchen" pack -s "$WORK/iso" -o "$WORK/out.iso" >/dev/null || exit 1
"$REPO_ROOT/kitchen" unpack "$WORK/out.iso" -o "$WORK/re" >/dev/null || exit 1

# A PRISTINE second extraction of the source is the reference. Comparing the rebuild
# against $WORK/iso instead would compare the build tree with itself: anything that
# corrupted the tree between unpack and pack would appear on both sides and pass. That
# is not hypothetical -- the first draft of this check did exactly that, and a
# deliberately corrupted file and a stripped exec bit both sailed through it.
"$REPO_ROOT/kitchen" unpack "$ISO" -o "$WORK/orig" >/dev/null || exit 1

python3 - "$ISO" "$WORK/out.iso" "$WORK/orig/iso" "$WORK/re/iso" "$REPO_ROOT" <<'PY'
import hashlib, os, subprocess, sys

orig_iso, new_iso, orig_tree, new_tree, repo_root = sys.argv[1:6]
sys.path.insert(0, os.path.join(repo_root, "lib"))
rc = 0


def fail(msg):
    global rc
    print(f"  FAIL {msg}")
    rc = 1


def walk(root):
    """path -> (sha256, mode, is_symlink target or None), relative to root."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for n in sorted(dirnames):
            p = os.path.join(dirpath, n)
            out["/" + os.path.relpath(p, root)] = ("<dir>", oct(os.lstat(p).st_mode & 0o7777), None)
        for n in sorted(filenames):
            p = os.path.join(dirpath, n)
            rel = "/" + os.path.relpath(p, root)
            st = os.lstat(p)
            if os.path.islink(p):
                out[rel] = ("<link>", oct(st.st_mode & 0o7777), os.readlink(p))
                continue
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 22), b""):
                    h.update(chunk)
            out[rel] = (h.hexdigest(), oct(st.st_mode & 0o7777), None)
    return out


so, sn = os.path.getsize(orig_iso), os.path.getsize(new_iso)
print(f"  size      original={so:,}  rebuilt={sn:,}  delta={sn - so:+,}")

a, b = walk(orig_tree), walk(new_tree)
# .kitchen/ is our own provenance sidecar, written next to the tree, never inside it.
a = {k: v for k, v in a.items() if not k.startswith("/.kitchen")}
b = {k: v for k, v in b.items() if not k.startswith("/.kitchen")}

missing = sorted(set(a) - set(b))
added = sorted(set(b) - set(a))
print(f"  contents  {len(a)} entries in the original, {len(b)} in the rebuild")
for p in missing[:10]:
    fail(f"lost in rebuild: {p}")
for p in added[:10]:
    fail(f"appeared in rebuild: {p}")

bad_content = [p for p in sorted(set(a) & set(b)) if a[p][0] != b[p][0]]
bad_mode = [p for p in sorted(set(a) & set(b)) if a[p][1] != b[p][1]]
bad_link = [p for p in sorted(set(a) & set(b)) if a[p][2] != b[p][2]]

for p in bad_content[:10]:
    fail(f"content changed: {p}  {a[p][0][:16]} -> {b[p][0][:16]}")
for p in bad_mode[:10]:
    fail(f"mode changed: {p}  {a[p][1]} -> {b[p][1]}")
for p in bad_link[:10]:
    fail(f"symlink target changed: {p}  {a[p][2]} -> {b[p][2]}")

if not (missing or added or bad_content or bad_mode or bad_link):
    print(f"  ok        every file byte-identical, every mode preserved")

# Structure: Rock Ridge, Joliet, identifiers, and the El Torito shape must survive.
from isoparse import IsoReader                                          # noqa: E402
with IsoReader(orig_iso) as ra, IsoReader(new_iso) as rb:
    ia, ib = ra.info(), rb.info()
    for field in ("volume_id", "system_id", "application_id", "rock_ridge", "joliet",
                  "volume_space"):
        va, vb = getattr(ia, field), getattr(ib, field)
        if va != vb:
            fail(f"{field}: {va!r} -> {vb!r}")
    ka = [(e.kind, e.platform_name, e.bootable, e.media) for e in ia.boot_entries]
    kb = [(e.kind, e.platform_name, e.bootable, e.media) for e in ib.boot_entries]
    if ka != kb:
        fail(f"El Torito shape: {ka} -> {kb}")
    else:
        print(f"  ok        structure preserved (RR={ia.rock_ridge} Joliet={ia.joliet} "
              f"vol={ia.volume_id!r}, {len(ka)} El Torito entries)")
    bt = ib.boot_info_table
    if not bt or not bt["self_consistent"]:
        fail("boot-info-table checksum is not self-consistent in the rebuild")
    else:
        print(f"  ok        boot-info-table self-consistent (0x{bt['checksum']:08x})")

# Layout position is informational: genisoimage allocates extents in directory-scan
# order, which varies by filesystem. A move is not a defect.
def first_lba(path):
    r = subprocess.run(["xorriso", "-indev", path, "-find", "/", "-exec", "report_lba", "--"],
                       capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        if line.startswith("File data"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                out[parts[4].strip("'")] = int(parts[1])
    return out

la, lb = first_lba(orig_iso), first_lba(new_iso)
moved = [p for p in la if p in lb and la[p] != lb[p]]
if moved:
    ex = moved[0]
    print(f"  note      {len(moved)} of {len(la)} files sit at a different LBA "
          f"(e.g. {ex}: {la[ex]} -> {lb[ex]}).")
    print(f"            Expected: extent order follows directory-scan order. Not a defect "
          f"-- see docs/40-workflow/reproducibility.md")

sys.exit(rc)
PY
