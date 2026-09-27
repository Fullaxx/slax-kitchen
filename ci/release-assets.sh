#!/bin/sh
# Gather what travels with published images into one flat directory, ready to upload.
#
#   ci/release-assets.sh <iso>... <outdir> [--no-image]
#
# Several images go into one set when they were built from the same kitchen and project
# commits -- one release is one version. What lands in <outdir>, every name safe to use as
# a GitHub Release asset:
#
#   for each image
#   <image>.iso                   the image itself (left out with --no-image)
#   <image>.iso.provenance.json   what the build fetched and built, as `kitchen pack` wrote it
#   <image>.sources.json          `kitchen sources`: every file in the image, classified
#   <image>.SOURCES.md            the same for people: where each part's source lives
#
#   once, shared by the images that need them (a source two images fetched must be the
#   same bytes in both, or nothing is assembled)
#   slax-kitchen-<commit>-source.tar.gz   the kitchen at the recorded commit, submodules in
#   <project>-<commit>-source.tar.gz      the project that vendors it, when there is one
#   <source>_<version>.source.tar one per source package of something built here (GRUB)
#   busybox-*.tar.bz2, *.config   a build claim's source and configuration, when present
#   release-index.json            every image, and every asset above: role, sha256, size,
#                                 what it covers and which images it serves
#   SHA256SUMS                    everything else in the directory
#
# A downstream project calls it as vendor/slax-kitchen/ci/release-assets.sh; the project
# is found as the kitchen's git superproject, or named by PROJECT_ROOT.
#
# It refuses rather than assembling a partial set: an image `kitchen sources` cannot
# account for, images built from different commits, a kitchen or project checkout with
# uncommitted changes (these scripts and the recipes are part of what is being attested),
# or a non-empty <outdir>. Check the result with ci/release-verify.py before uploading
# anything.
#
# Nothing here uploads. Publishing is a decision a person makes, and
# docs/40-workflow/publishing-images.md is what they should read first.
set -eu
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

die() { printf 'release-assets: %s\n' "$*" >&2; exit 1; }
usage() { echo "usage: ci/release-assets.sh <iso>... <outdir> [--no-image]" >&2; exit 2; }

IMAGE=1 OUT="" n=0
for a in "$@"; do
    case "$a" in
        --no-image) IMAGE=0 ;;
        -h|--help)  usage ;;
        -*)         echo "release-assets: unknown option $a" >&2; usage ;;
        *)          n=$((n + 1)); OUT=$a ;;
    esac
done
[ "$n" -ge 2 ] || usage
# "$@" becomes the images alone: every positional but the last, in order. The list the
# loop walks is fixed when it starts, so shifting the old arguments off and appending the
# kept ones is safe.
i=0
for a in "$@"; do
    shift
    case "$a" in -*) continue ;; esac
    i=$((i + 1))
    if [ "$i" -lt "$n" ]; then set -- "$@" "$a"; fi
done
for ISO in "$@"; do
    [ -f "$ISO" ] || die "no such image: $ISO"
    [ -f "$ISO.provenance.json" ] || die "no $ISO.provenance.json -- \`kitchen pack\` writes it beside the image"
done

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

# Each image accounted for on its own, into its own directory: $TMP/1 for the first.
i=0
for ISO in "$@"; do
    i=$((i + 1))
    mkdir "$TMP/$i"
    printf 'release-assets: accounting for every file in %s\n' "$ISO"
    python3 "$REPO_ROOT/lib/sources.py" "$ISO" --fetch "$TMP/$i/fetch" \
            --json "$TMP/$i/sources.json" --markdown "$TMP/$i/SOURCES.md" \
        || die "\`kitchen sources\` did not account for everything in $ISO, so nothing was assembled"
done

python3 - "$OUT" "$TMP" "$IMAGE" "$@" <<'PY'
import hashlib, json, os, re, shutil, sys, tarfile

out, tmp, image, isos = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4:]
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
    rec = {"name": name, "role": role, "sha256": sha256(p), "size": os.path.getsize(p),
           **{k: v for k, v in detail.items() if v}}
    assets.append(rec)
    return rec


bases = [os.path.basename(i) for i in isos]
for b in sorted(set(bases)):
    if bases.count(b) > 1:
        sys.exit(f"release-assets: two images are named {b}; each needs its own name in one "
                 "release")


# ONE RELEASE IS ONE VERSION. The kitchen and project archives are per commit, and every
# image in a set shares them. Images from different commits would need an archive of each,
# and a set built that way is two releases wearing one tag.
def commits(iso):
    p = json.load(open(iso + ".provenance.json"))
    return tuple((p.get(k) or {}).get("commit") for k in ("kitchen", "project"))


first = commits(isos[0])
for iso, b in zip(isos[1:], bases[1:]):
    c = commits(iso)
    if c != first:
        sys.exit(f"release-assets: {b} was built from kitchen {(c[0] or '?')[:12]} and "
                 f"project {(c[1] or 'none')[:12]}, and {bases[0]} from kitchen "
                 f"{(first[0] or '?')[:12]} and project {(first[1] or 'none')[:12]}; one "
                 "release is one version, so its images come from the same commits")


def source_tar(p, n, dest):
    """A source package is several files whose names the .dsc depends on, and some of those
    names (~, +) are not ones GitHub keeps. One tar per package keeps them -- and the same
    files always make the same tar, which is what lets two images' copies be compared."""
    members = sorted(os.listdir(p))
    with tarfile.open(dest, "w", format=tarfile.GNU_FORMAT) as t:
        for m in members:
            info = t.gettarinfo(os.path.join(p, m), arcname=f"{n}/{m}")
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            info.mode = 0o644
            with open(os.path.join(p, m), "rb") as fh:
                t.addfile(info, fh)
    return [f"{n}/{m}" for m in members]


# THE SOURCES EVERY IMAGE FETCHED, ONCE. Each record says which images it serves, so a
# verifier can tell the source of what one image built from the source of what another did.
shared = {}
for i, b in enumerate(bases, 1):
    doc = json.load(open(os.path.join(tmp, str(i), "sources.json")))
    fetched = {a["file"]: a for a in doc.get("assets") or []}
    fetch = os.path.join(tmp, str(i), "fetch")
    for n in sorted(os.listdir(fetch)) if os.path.isdir(fetch) else []:
        p = os.path.join(fetch, n)
        if os.path.isdir(p):
            name = re.sub(r"[^A-Za-z0-9._-]", "_", n) + ".source.tar"
            src = os.path.join(tmp, str(i), name)
            contained = source_tar(p, n, src)
            # `what` and `for` are what the sources document says about each fetched
            # file; a record carrying neither is still a file in the tar, so ask with .get().
            what = {w for c in contained if (w := fetched.get(c, {}).get("what"))}
            covers = {f for c in contained if (f := fetched.get(c, {}).get("for"))}
        else:
            name, src, contained = n, p, None
            a = fetched.get(n, {})
            what = {a["what"]} if a.get("what") else set()
            covers = {a["for"]} if a.get("for") else set()
        rec = shared.get(name)
        if rec is None:
            place(src, name)
            rec = shared[name] = add(name, "source", contains=contained)
            rec["images"] = []
        elif sha256(src) != rec["sha256"]:
            sys.exit(f"release-assets: {rec['images'][0]} and {b} both fetched {name}, and the "
                     "two are not the same bytes; one set cannot carry both under one name")
        for key, got in (("what", what), ("covers", covers)):
            merged = sorted(set(rec.get(key) or []) | got)
            if merged:
                rec[key] = merged
        rec["images"].append(b)

# EACH IMAGE'S OWN RECORDS, named after it, and linked from the index.
images, kitchen = [], None
for i, (iso, b) in enumerate(zip(isos, bases), 1):
    stem = b[:-4] if b.endswith(".iso") else b
    doc = json.load(open(os.path.join(tmp, str(i), "sources.json")))
    kitchen = kitchen or doc.get("kitchen")
    img = doc.get("image") or {}
    entry = {"name": b, "sha256": img.get("sha256"), "size": img.get("size"),
             "attached": image}
    for key, src, name, role in (
            ("provenance", iso + ".provenance.json", b + ".provenance.json", "provenance"),
            ("sources", os.path.join(tmp, str(i), "sources.json"), stem + ".sources.json",
             "sources"),
            ("sources_md", os.path.join(tmp, str(i), "SOURCES.md"), stem + ".SOURCES.md",
             "sources")):
        place(src, name)
        add(name, role)["images"] = [b]
        entry[key] = name
    if image:
        place(iso, b, link=True)
        add(b, "image")["images"] = [b]
    images.append(entry)

index = {
    "schema": "slax-kitchen/release-index/v2",
    "images": images,
    "kitchen": kitchen,
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
print(f"  {'index':<10} release-index.json ({len(images)} image{'s' if len(images) > 1 else ''})")
print(f"  {'sums':<10} SHA256SUMS")
PY
printf 'release-assets: wrote %s -- check it with ci/release-verify.py before uploading\n' "$OUT"
