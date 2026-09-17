#!/bin/sh
# Gather what travels with a published image into one flat directory, ready to upload.
#
#   ci/release-assets.sh <iso> <outdir> [--no-image]
#
# What lands in <outdir>, every name safe to use as a GitHub Release asset:
#
#   <image>.iso                   the image itself (left out with --no-image)
#   <image>.iso.provenance.json   what the build fetched and built, as `kitchen pack` wrote it
#   <image>.sources.json          `kitchen sources`: every file in the image, classified
#   <image>.SOURCES.md            the same for people: where each part's source lives
#   slax-kitchen-<commit>-source.tar.gz   the kitchen at the recorded commit, submodules in
#   <project>-<commit>-source.tar.gz      the project that vendors it, when there is one
#   <source>_<version>.source.tar one per source package of something built here (GRUB)
#   busybox-*.tar.bz2, *.config   a build claim's source and configuration, when present
#   release-index.json            every asset above: role, sha256, size, what it covers
#   SHA256SUMS                    everything else in the directory
#
# A downstream project calls it as vendor/slax-kitchen/ci/release-assets.sh; the project
# is found as the kitchen's git superproject, or named by PROJECT_ROOT.
#
# It refuses rather than assembling a partial set: an image `kitchen sources` cannot
# account for, a kitchen or project checkout with uncommitted changes (these scripts and
# the recipes are part of what is being attested), or a non-empty <outdir>. Check the
# result with ci/release-verify.py before uploading anything.
#
# Nothing here uploads. Publishing is a decision a person makes, and
# docs/40-workflow/publishing-images.md is what they should read first.
set -eu
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

die() { printf 'release-assets: %s\n' "$*" >&2; exit 1; }
usage() { echo "usage: ci/release-assets.sh <iso> <outdir> [--no-image]" >&2; exit 2; }

ISO="" OUT="" IMAGE=1
while [ $# -gt 0 ]; do
    case "$1" in
        --no-image) IMAGE=0; shift ;;
        -h|--help)  usage ;;
        -*)         echo "release-assets: unknown option $1" >&2; usage ;;
        *)  if [ -z "$ISO" ]; then ISO=$1; elif [ -z "$OUT" ]; then OUT=$1; else usage; fi
            shift ;;
    esac
done
if [ -z "$ISO" ] || [ -z "$OUT" ]; then usage; fi
[ -f "$ISO" ] || die "no such image: $ISO"
[ -f "$ISO.provenance.json" ] || die "no $ISO.provenance.json -- \`kitchen pack\` writes it beside the image"

# The checkouts must be commits: what is attested is the recorded commit, and the scripts
# doing the attesting are part of it. Ignored files (out/, work/, build/) do not count.
clean_or_die() {
    _st=$(git -C "$1" status --porcelain --untracked-files=normal 2>&1) \
        || die "git cannot read $1: $_st"
    [ -z "$_st" ] || die "$2 has uncommitted changes:
$(printf '%s\n' "$_st" | head -10)"
}
clean_or_die "$REPO_ROOT" "the kitchen checkout"
PROJECT=${PROJECT_ROOT:-$(git -C "$REPO_ROOT" rev-parse --show-superproject-working-tree 2>/dev/null || true)}
[ -z "$PROJECT" ] || clean_or_die "$PROJECT" "the project checkout ($PROJECT)"

if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
    die "$OUT is not empty; assemble into a fresh directory"
fi
mkdir -p "$OUT"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-assets.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

printf 'release-assets: accounting for every file in %s\n' "$ISO"
python3 "$REPO_ROOT/lib/sources.py" "$ISO" --fetch "$TMP/fetch" \
        --json "$TMP/sources.json" --markdown "$TMP/SOURCES.md" \
    || die "\`kitchen sources\` did not account for everything, so nothing was assembled"

python3 - "$ISO" "$OUT" "$TMP" "$IMAGE" <<'PY'
import hashlib, json, os, re, shutil, sys, tarfile

iso, out, tmp, image = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
# GitHub renames an uploaded asset whose name has other characters, and SHA256SUMS would
# then name a file that is not there. Refuse instead.
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# Written at the end from what the other assets are, so nothing else may take these two
# names: a fetched file called SHA256SUMS would be placed, hashed into the index, and then
# overwritten by the index's own sums file.
RESERVED = {"SHA256SUMS", "release-index.json"}


def asset_path(name):
    """Where an asset goes, once its name is one GitHub will keep and nothing else claims
    it. Both the files copied here and the tars built here go through this: a name GitHub
    rewrites leaves SHA256SUMS naming a file that is not in the release."""
    if not SAFE.match(name):
        sys.exit(f"release-assets: {name!r} is not a name GitHub keeps as it is")
    if name in RESERVED:
        sys.exit(f"release-assets: {name} is written by this script; an asset cannot take "
                 "that name")
    dest = os.path.join(out, name)
    if os.path.exists(dest):
        sys.exit(f"release-assets: two assets would be named {name}")
    return dest


def place(src, name, link=False):
    dest = asset_path(name)
    if link:
        try:
            os.link(src, dest)
            return dest
        except OSError:
            pass
    shutil.copy2(src, dest)
    return dest


assets = []


def add(name, role, **detail):
    p = os.path.join(out, name)
    assets.append({"name": name, "role": role, "sha256": sha256(p), "size": os.path.getsize(p),
                   **{k: v for k, v in detail.items() if v}})


doc = json.load(open(os.path.join(tmp, "sources.json")))
fetched = {a["file"]: a for a in doc.get("assets") or []}
fetch = os.path.join(tmp, "fetch")
base = os.path.basename(iso)
stem = base[:-4] if base.endswith(".iso") else base

for n in sorted(os.listdir(fetch)):
    p = os.path.join(fetch, n)
    if os.path.isdir(p):
        # A source package is several files whose names the .dsc depends on, and some of
        # those names (~, +) are not ones GitHub keeps. One tar per package keeps them.
        members = sorted(os.listdir(p))
        name = re.sub(r"[^A-Za-z0-9._-]", "_", n) + ".source.tar"
        dest = asset_path(name)
        with tarfile.open(dest, "w", format=tarfile.GNU_FORMAT) as t:
            for m in members:
                info = t.gettarinfo(os.path.join(p, m), arcname=f"{n}/{m}")
                info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
                info.mode = 0o644
                with open(os.path.join(p, m), "rb") as fh:
                    t.addfile(info, fh)
        contained = [f"{n}/{m}" for m in members]
        # `what` and `for` are what the sources document says about each fetched file;
        # a record carrying neither is still a file in the tar, so ask with .get().
        add(name, "source",
            what=sorted({w for c in contained if (w := fetched.get(c, {}).get("what"))}),
            covers=sorted({f for c in contained if (f := fetched.get(c, {}).get("for"))}),
            contains=contained)
    else:
        place(p, n)
        a = fetched.get(n, {})
        add(n, "source", what=[a["what"]] if a.get("what") else None,
            covers=[a["for"]] if a.get("for") else None)

place(iso + ".provenance.json", base + ".provenance.json")
add(base + ".provenance.json", "provenance")
place(os.path.join(tmp, "sources.json"), stem + ".sources.json")
add(stem + ".sources.json", "sources")
place(os.path.join(tmp, "SOURCES.md"), stem + ".SOURCES.md")
add(stem + ".SOURCES.md", "sources")
if image:
    place(iso, base, link=True)
    add(base, "image")

img = doc.get("image") or {}
index = {
    "schema": "slax-kitchen/release-index/v1",
    "image": {"name": img.get("name"), "sha256": img.get("sha256"), "size": img.get("size"),
              "attached": image},
    "kitchen": doc.get("kitchen"),
    "assets": sorted(assets, key=lambda a: a["name"]),
}
with open(os.path.join(out, "release-index.json"), "w") as f:
    json.dump(index, f, indent=1, sort_keys=True)
    f.write("\n")

with open(os.path.join(out, "SHA256SUMS"), "w") as f:
    for n in sorted(os.listdir(out)):
        if n != "SHA256SUMS":
            f.write(f"{sha256(os.path.join(out, n))}  {n}\n")

for a in index["assets"]:
    print(f"  {a['role']:<10} {a['name']}")
print(f"  {'index':<10} release-index.json")
print(f"  {'sums':<10} SHA256SUMS")
PY
printf 'release-assets: wrote %s -- check it with ci/release-verify.py before uploading\n' "$OUT"
