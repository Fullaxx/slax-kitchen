#!/usr/bin/env python3
"""Unit tests for ci/release-verify.py and ci/redistribution-claim.py, on a fixture
assets directory -- no ISO, no network.

The fixture is what ci/release-assets.sh writes for an image with one built component
(GRUB's EFI image) and the kitchen tree with one submodule. Each negative case breaks it
in exactly one way that a real release could, and requires the gate to name it.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ci"))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import importlib.util  # noqa: E402
import traceback


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


verify_mod = _load("release_verify", os.path.join(ROOT, "ci", "release-verify.py"))
claim_mod = _load("redistribution_claim", os.path.join(ROOT, "ci", "redistribution-claim.py"))

FAILURES = []
IMG = "e" * 64
COMMIT = "a" * 40
LIVEKIT = "b" * 40


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def mentions(problems, needle):
    return any(needle in p for p in problems)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def tar_add(t, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    t.addfile(info, io.BytesIO(data))


def build(d, *, names=("x",), attached=False, submodule_files=True, firmware=None, extra=None,
          prov_extra=None, src_extra=None):
    """Write a passing assets directory into d, with optional breakage. Each name is an
    image, and the kitchen archive and GRUB's source are shared by all of them, the way
    ci/release-assets.sh writes a set of images built from one commit."""
    kit = "slax-kitchen-aaaaaaaaaaaa-source.tar.gz"
    with tarfile.open(os.path.join(d, kit), "w:gz") as t:
        tar_add(t, "slax-kitchen/README.md", b"readme\n")
        if submodule_files:
            tar_add(t, "slax-kitchen/vendor/linux-live/build", b"#!/bin/sh\n")
        tar_add(t, "slax-kitchen/SUBMODULES.txt", f"{LIVEKIT} vendor/linux-live\n".encode())
    grub = "grub2-unsigned_2.12-1ubuntu7.3.source.tar"
    with tarfile.open(os.path.join(d, grub), "w") as t:
        tar_add(t, "grub2-unsigned_2.12-1ubuntu7.3/grub2-unsigned_2.12-1ubuntu7.3.dsc", b"Format: 3.0\n")
    isos = [f"{n}.iso" for n in names]
    roles = {kit: ("source", ["(kitchen)"], ["slax-kitchen at the recorded commit, with submodules"],
                   isos),
             grub: ("source", ["boot/efi.img"], ["source of GRUB EFI image"], isos)}
    images = []
    for i, n in enumerate(names):
        iso = f"{n}.iso"
        if attached:
            open(os.path.join(d, iso), "wb").write(f"iso {n}".encode())
            roles[iso] = ("image", None, None, [iso])
        # An attached image has to be the one the records describe.
        image_sha = sha(os.path.join(d, iso)) if attached else (IMG if i == 0 else f"{i:x}" * 64)
        prov = {"schema": "slax-kitchen/provenance/v1",
                "kitchen": {"commit": COMMIT, "describe": "aaaaaaa", "dirty": False,
                            "submodules": {"vendor/linux-live": LIVEKIT}},
                "pack": {"iso": {"name": iso, "sha256": image_sha, "size": 3}}}
        prov.update(prov_extra or {})
        src = {"schema": "slax-kitchen/sources/v1", "image": {"name": iso, "sha256": image_sha},
               "components": [{"path": "boot/efi.img", "class": "built", "what": "GRUB EFI image"},
                              {"path": "slax/modules/01-core.sb", "class": "slax"}],
               "unresolved": [], "not_redistributable": [], "warnings": [],
               "firmware": firmware or {"stock_bundle": False, "license_texts": []}}
        src.update(src_extra or {})
        json.dump(prov, open(os.path.join(d, f"{iso}.provenance.json"), "w"))
        json.dump(src, open(os.path.join(d, f"{n}.sources.json"), "w"))
        open(os.path.join(d, f"{n}.SOURCES.md"), "w").write("# Sources\n")
        roles[f"{iso}.provenance.json"] = ("provenance", None, None, [iso])
        roles[f"{n}.sources.json"] = ("sources", None, None, [iso])
        roles[f"{n}.SOURCES.md"] = ("sources", None, None, [iso])
        images.append({"name": iso, "sha256": image_sha, "size": 3, "attached": attached,
                       "provenance": f"{iso}.provenance.json", "sources": f"{n}.sources.json",
                       "sources_md": f"{n}.SOURCES.md"})
    for name, (content, role) in (extra or {}).items():
        open(os.path.join(d, name), "wb").write(content)
        roles[name] = (role, None, None, None)
    assets = []
    for name, (role, covers, what, serves) in sorted(roles.items()):
        a = {"name": name, "role": role}
        if covers:
            a["covers"] = covers
        if what:
            a["what"] = what
        if serves:
            a["images"] = serves
        assets.append(a)
    json.dump({"schema": "slax-kitchen/release-index/v2", "images": images, "assets": assets},
              open(os.path.join(d, "release-index.json"), "w"))
    rehash(d)


def rehash(d):
    """Put every asset's sha256 and size in the index, and write SHA256SUMS, after a test
    has edited a file on purpose -- so that edit is the only thing wrong."""
    path = os.path.join(d, "release-index.json")
    idx = json.load(open(path))
    for a in idx["assets"]:
        f = os.path.join(d, a["name"])
        if os.path.isfile(f):
            a["sha256"], a["size"] = sha(f), os.path.getsize(f)
    json.dump(idx, open(path, "w"))
    write_sums(d)


def write_sums(d):
    with open(os.path.join(d, "SHA256SUMS"), "w") as f:
        for n in sorted(os.listdir(d)):
            if n != "SHA256SUMS":
                f.write(f"{sha(os.path.join(d, n))}  {n}\n")


class Fixture:
    def __init__(self, **kw):
        self.d = tempfile.mkdtemp()
        build(self.d, **kw)

    def __enter__(self):
        return self.d

    def __exit__(self, *exc):
        shutil.rmtree(self.d, ignore_errors=True)


def assembler():
    """The python program embedded in ci/release-assets.sh, so the naming rules it applies
    can be exercised without an ISO, a network or a clean checkout."""
    text = open(os.path.join(ROOT, "ci", "release-assets.sh")).read()
    body = text.split("<<'PY'\n", 1)[1]
    return body.split("\nPY\n", 1)[0]


def assemble(fetch_tree, assets_records, more=(), commits=None):
    """Run that program over fetch directories, as release-assets.sh does: one image's
    fetch tree and sources records, plus `more` (tree, records) pairs for further images,
    each built from the kitchen commit `commits` names for it. Returns (returncode, output,
    the directory it wrote)."""
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    work = os.path.join(tmp, "tmp")
    os.makedirs(out)
    isos = []
    for i, (tree, records) in enumerate([(fetch_tree, assets_records), *more], 1):
        os.makedirs(os.path.join(work, str(i), "fetch"))
        for path, data in tree.items():
            full = os.path.join(work, str(i), "fetch", path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w").write(data)
        iso = os.path.join(tmp, f"slax-x{i}-1.0.iso")
        json.dump({"assets": records, "kitchen": {"commit": COMMIT},
                   "image": {"name": os.path.basename(iso), "sha256": IMG, "size": 1}},
                  open(os.path.join(work, str(i), "sources.json"), "w"))
        open(os.path.join(work, str(i), "SOURCES.md"), "w").write("# sources\n")
        open(iso, "w").write("not really an image")
        commit = (commits or [])[i - 1] if commits else COMMIT
        json.dump({"kitchen": {"commit": commit}}, open(iso + ".provenance.json", "w"))
        isos.append(iso)
    prog = os.path.join(tmp, "assemble.py")
    open(prog, "w").write(assembler())
    r = subprocess.run([sys.executable, prog, out, work, "0", *isos],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr, out


def test_a_source_tar_is_named_the_way_every_other_asset_is():
    """One branch built its tar's name with its own re.sub and wrote it straight out,
    skipping the SAFE check every other asset goes through: a package directory beginning
    with a character GitHub rewrites produced `_foo.source.tar`, which GitHub renames,
    leaving SHA256SUMS naming a file that is not in the release."""
    rc, out, _ = assemble({"~foo-1.0/a.dsc": "x"}, [])
    check("refused", rc, 1)
    check("and says why", "is not a name GitHub keeps" in out, True)

    rc, _out, dest = assemble({"grub2_2.12-1ubuntu7.3/g.dsc": "x"}, [])
    check("an ordinary package is assembled", rc, 0)
    check("under a safe name", "grub2_2.12-1ubuntu7.3.source.tar" in os.listdir(dest), True)


def test_nothing_may_take_the_name_of_a_file_this_script_writes():
    """SHA256SUMS and release-index.json are written last, from what the other assets are.
    A fetched file with one of those names was copied in, hashed into the index, and then
    overwritten -- leaving an index whose record of itself was of a file that is gone."""
    rc, out, _ = assemble({"SHA256SUMS": "not really sums"}, [])
    check("refused", rc, 1)
    check("and says whose name it is", "written by this script" in out, True)


def test_a_fetched_file_the_sources_document_says_nothing_about():
    """The set comprehension asked `fetched[c]["what"]` for every record it found. A
    record without that key -- the document does not require one -- was a KeyError in the
    middle of assembling a release."""
    rc, out, dest = assemble(
        {"grub2_2.12/g.dsc": "x", "grub2_2.12/g.tar.xz": "y"},
        [{"file": "grub2_2.12/g.dsc"},                       # no `what`, no `for`
         {"file": "grub2_2.12/g.tar.xz", "what": "GRUB", "for": "slax/boot/bootx64.efi"}])
    check("assembled", (rc, "Traceback" in out), (0, False))
    index = json.load(open(os.path.join(dest, "release-index.json")))
    tar = [a for a in index["assets"] if a["name"].endswith(".source.tar")][0]
    check("says what it could", (tar["what"], tar["covers"]),
          (["GRUB"], ["slax/boot/bootx64.efi"]))


def test_a_complete_directory_passes():
    with Fixture() as d:
        check("no problems", verify_mod.verify(d, assert_no_images=True), [])


def test_sha256sums_must_be_exact():
    with Fixture() as d:
        os.remove(os.path.join(d, "grub2-unsigned_2.12-1ubuntu7.3.source.tar"))
        p = verify_mod.verify(d)
        check("a missing asset is named", mentions(p, "which is not here"), True)
    with Fixture() as d:
        lines = open(os.path.join(d, "SHA256SUMS")).read().splitlines()
        lines[0] = "0" * 64 + lines[0][64:]
        open(os.path.join(d, "SHA256SUMS"), "w").write("\n".join(lines) + "\n")
        check("a wrong hash is named", mentions(verify_mod.verify(d), "does not match SHA256SUMS"), True)
    with Fixture() as d:
        open(os.path.join(d, "stray.txt"), "w").write("x")
        check("an unlisted file is named", mentions(verify_mod.verify(d), "does not list stray.txt"), True)


def test_what_the_manifest_refuses_is_refused():
    with Fixture(src_extra={"not_redistributable": [{"recipe": "all-browsers",
                                                     "why": "proprietary browsers"}]}) as d:
        check("a disallowed recipe", mentions(verify_mod.verify(d), "all-browsers must not be"), True)
    with Fixture(src_extra={"unresolved": [{"path": "x", "reason": "y"}]}) as d:
        check("anything unresolved", mentions(verify_mod.verify(d), "unresolved in the image"), True)


def test_built_here_needs_its_source():
    with Fixture() as d:
        idx = json.load(open(os.path.join(d, "release-index.json")))
        for a in idx["assets"]:
            a.pop("covers", None) if a["name"].startswith("grub2") else None
        json.dump(idx, open(os.path.join(d, "release-index.json"), "w"))
        write_sums(d)
        check("GRUB without its source", mentions(verify_mod.verify(d),
                                                  "boot/efi.img was built here"), True)


def test_the_project_archive_carries_its_submodules():
    """GitHub's automatic archives leave submodules out; this is what catches ours doing it."""
    with Fixture(submodule_files=False) as d:
        check("an empty submodule", mentions(verify_mod.verify(d),
                                             "submodule vendor/linux-live is empty"), True)
    with Fixture(prov_extra={"kitchen": {"commit": COMMIT, "describe": "x", "dirty": False,
                                         "submodules": {"vendor/linux-live": "c" * 40}}}) as d:
        check("different pins", mentions(verify_mod.verify(d), "does not match the submodule pins"), True)


def test_no_images_is_checked_by_content_not_by_name():
    elf = b"\x7fELF" + b"\0" * 100
    iso = b"\0" * 0x8001 + b"CD001" + b"\0" * 100
    with Fixture(extra={"notes.txt": (elf, "source")}) as d:
        check("an ELF named .txt", mentions(verify_mod.verify(d, assert_no_images=True),
                                             "notes.txt is an ELF executable"), True)
        check("only with the flag", mentions(verify_mod.verify(d), "ELF"), False)
    with Fixture(extra={"readme.md": (iso, "source")}) as d:
        check("an ISO named .md", mentions(verify_mod.verify(d, assert_no_images=True),
                                            "is an ISO 9660 image"), True)
    with Fixture(attached=True) as d:
        check("an attached image passes without the flag", verify_mod.verify(d), [])
        check("and fails with it", mentions(verify_mod.verify(d, assert_no_images=True),
                                            "an image is attached"), True)


def test_anything_that_is_not_a_regular_file_is_refused():
    """Every check works off the file list, so a directory or a symlink used to be invisible
    to all of them -- while the CI upload step takes the directory recursively."""
    with Fixture() as d:
        os.mkdir(os.path.join(d, "extra"))
        open(os.path.join(d, "extra", "smuggled.iso"), "wb").write(b"\0" * 0x8001 + b"CD001")
        p = verify_mod.verify(d, assert_no_images=True)
        check("the directory is named", mentions(p, "extra"), True)
        # `len(p) >= 1` used to stand here, which cannot fail independently: mentions()
        # is falsy on an empty list, so the line above had already failed. Assert the
        # count instead -- one problem, for the one thing wrong.
        check("and it is the only problem", len(p), 1)


def test_the_content_scan_runs_even_when_the_records_are_broken():
    """--assert-no-images used to return early on an unreadable provenance, so the operator
    heard about the JSON and not about the ISO sitting beside it."""
    with Fixture(extra={"readme.md": (b"\0" * 0x8001 + b"CD001", "source")}) as d:
        open(os.path.join(d, "x.iso.provenance.json"), "w").write("{ not json")
        write_sums(d)
        p = verify_mod.verify(d, assert_no_images=True)
        check("the unreadable record is reported", mentions(p, "not readable JSON"), True)
        check("and so is the ISO content", mentions(p, "is an ISO 9660 image"), True)


def test_no_host_paths_and_no_renamed_names():
    # A place on THIS machine: the rule compares against where the machine publishing really
    # is, not against what a path looks like. #26 retired the shape rule this used to test.
    with Fixture(prov_extra={"note": os.path.join(ROOT, "build", "x.iso")}) as d:
        check("a host path", mentions(verify_mod.verify(d), "names a place on this machine"), True)
    # ...and the image's own paths are not one. The shape rule refused this var here even
    # after #20 had exempted it at apply time, so boot-matrix could never have been released.
    in_image = [{"recipe": "testkit", "vars": {"marker": "/var/lib/kitchen-perch-marker"}},
                {"recipe": "r", "artifacts": ["slax/rootcopy/root/.bashrc"]}]
    with Fixture(prov_extra={"recipes": in_image}) as d:
        check("an in-image path is not a host path",
              mentions(verify_mod.verify(d), "names a place on this machine"), False)
    with Fixture(extra={"a~b.txt": (b"x", "source")}) as d:
        check("a name GitHub renames", mentions(verify_mod.verify(d), "a~b.txt: GitHub would rename"), True)


def test_an_attached_image_carries_its_firmware_licenses():
    fw = {"stock_bundle": True, "license_texts": []}
    with Fixture(attached=True, firmware=fw) as d:
        check("stock firmware alone", mentions(verify_mod.verify(d),
                                               "without the copyright files of its Debian firmware"), True)
    with Fixture(attached=False, firmware=fw) as d:
        check("not a question without an image", mentions(verify_mod.verify(d), "firmware"), False)


def test_the_claim_names_what_is_attached():
    with Fixture() as d:
        text = claim_mod.claim(d)
        check("no image, said plainly", "No image is attached to this release" in text, True)
        for n in ("slax-kitchen-aaaaaaaaaaaa-source.tar.gz", "x.iso.provenance.json", "x.SOURCES.md"):
            check(f"names {n}", f"`{n}`" in text, True)
        check("never claims completeness", "complete corresponding source" in text.lower(), False)
    with Fixture(attached=True) as d:
        text = claim_mod.claim(d)
        check("an attached image is named with its hash", "This release attaches `x.iso`" in text, True)


def test_the_claim_says_only_what_the_directory_shows():
    """Found on the first real run, on the tor assets: the section said "The license texts are
    inside the image" about an image whose firmware texts Slax's build had removed, and "does
    not identify itself as an official Slax release" about an image with volume id `slax`.
    Neither was read off anything."""
    stock = {"stock_bundle": True, "license_texts": []}
    with Fixture(firmware=stock) as d:
        text = claim_mod.claim(d)
        check("no license-text claim without the texts", "copyright files for its firmware packages are"
              in text, False)
        check("says what is true instead", "removed the license texts" in text, True)
        check("no identity claim it cannot check", "official Slax release" in text, False)
    restored = {"stock_bundle": True, "license_texts": [{"recipe": "firmware-refresh",
                                                         "bundle": "slax/modules/09-firmware-debian.sb",
                                                         "packages": ["firmware-iwlwifi"]}]}
    with Fixture(firmware=restored) as d:
        text = claim_mod.claim(d)
        check("the texts, when they are there", "copyright files for its firmware packages are inside"
              in text, True)
        # Kept by the owner's decision, as Slax shipped them: the b43 files are in the image,
        # and no license text came with them. The notes say so rather than implying otherwise.
        check("b43 said plainly", "b43" in text and "no license text" in text, True)


def test_release_notes_use_the_claim_when_given_assets():
    with Fixture(attached=True) as d:
        p = subprocess.run([os.path.join(ROOT, "ci", "release-notes.sh"), "v9.9.9"], cwd=ROOT,
                           capture_output=True, text=True,
                           env=dict(os.environ, REPO_ROOT=ROOT, RELEASE_ASSETS=d))
        check("renders", p.returncode, 0)
        check("the claim is in the notes", "This release attaches `x.iso`" in p.stdout, True)
        check("and the toolkit sentence is not", "a release of slax-kitchen is the toolkit" in p.stdout,
              False)
        # The Provenance section says its own thing about checksums, four lines above the
        # Redistribution section. It used to say "No image is attached to this release" in
        # the same notes that attach one.
        check("no contradiction with the provenance section",
              "No image is attached to this release" in p.stdout, False)
        check("it names the attached image's checksum instead",
              "SHA256SUMS" in p.stdout and "not byte-reproducible" in p.stdout, True)


def test_several_images_built_from_one_commit_are_one_set():
    """#54: the procedure held one image per release. A second image was refused ("not
    empty"), and a hand-merged set failed verification ("expected one provenance asset")
    while redistribution-claim.py described the first image alone. slax-wine builds five
    images from one commit. Now they go in together: each image's records, and the sources
    they share once, saying which images each serves."""
    grub = {"grub2_2.12/g.dsc": "x"}
    rec = [{"file": "grub2_2.12/g.dsc", "what": "GRUB", "for": "boot/efi.img"}]
    rc, out, dest = assemble(grub, rec, more=[(grub, rec)])
    check("two images assemble", (rc, "Traceback" in out), (0, False))
    index = json.load(open(os.path.join(dest, "release-index.json")))
    check("the index is v2 and lists both", (index["schema"],
                                             [im["name"] for im in index["images"]]),
          ("slax-kitchen/release-index/v2", ["slax-x1-1.0.iso", "slax-x2-1.0.iso"]))
    tars = [a for a in index["assets"] if a["name"].endswith(".source.tar")]
    check("the shared source is carried once, serving both",
          [(a["name"], a["images"]) for a in tars],
          [("grub2_2.12.source.tar", ["slax-x1-1.0.iso", "slax-x2-1.0.iso"])])
    for im in index["images"]:
        check(f"{im['name']} has its own records",
              all(os.path.isfile(os.path.join(dest, im[k]))
                  for k in ("provenance", "sources", "sources_md")), True)


def test_images_from_different_commits_are_not_one_release():
    """The kitchen and project archives are per commit, so a set is one version: images
    built from two commits would need two of each and say nothing about which is which."""
    rc, out, _ = assemble({}, [], more=[({}, [])], commits=[COMMIT, "c" * 40])
    check("refused", rc, 1)
    check("naming both and why", "one release is one version" in out
          and "slax-x2-1.0.iso" in out, True)


def test_a_source_two_images_fetched_differently_is_refused():
    """A set carries a shared source once, under one name. If two images fetched different
    bytes under that name, one of them would be described by the other's source."""
    rc, out, _ = assemble({"grub2_2.12/g.dsc": "x"}, [],
                          more=[({"grub2_2.12/g.dsc": "not x"}, [])])
    check("refused", rc, 1)
    check("naming the source", "grub2_2.12.source.tar" in out and "not the same bytes" in out,
          True)


def test_each_image_in_a_set_is_held_to_its_own_records():
    """The per-image half of #54: every image's provenance, sources manifest and built
    parts are checked for that image, and a source counts only for the images it serves."""
    with Fixture(names=("x", "y")) as d:
        check("a set of two images passes", verify_mod.verify(d, assert_no_images=True), [])
    with Fixture(names=("x", "y")) as d:
        path = os.path.join(d, "y.iso.provenance.json")
        doc = json.load(open(path))
        doc["pack"]["iso"]["sha256"] = IMG               # x's image, in y's record
        json.dump(doc, open(path, "w"))
        rehash(d)
        check("a record for the other image is named",
              mentions(verify_mod.verify(d), "y.iso: its provenance record is for a different"),
              True)
    with Fixture(names=("x", "y")) as d:
        idx = json.load(open(os.path.join(d, "release-index.json")))
        for a in idx["assets"]:
            if a["name"].startswith("grub2"):
                a["images"] = ["x.iso"]
        json.dump(idx, open(os.path.join(d, "release-index.json"), "w"))
        write_sums(d)
        p = verify_mod.verify(d)
        check("GRUB's source serving only x leaves y's GRUB unaccounted",
              (mentions(p, "y.iso: boot/efi.img was built here"),
               mentions(p, "x.iso: boot/efi.img")), (True, False))
    with Fixture(names=("x", "y")) as d:
        path = os.path.join(d, "y.iso.provenance.json")
        doc = json.load(open(path))
        doc["kitchen"]["commit"] = "c" * 40
        json.dump(doc, open(path, "w"))
        rehash(d)
        check("images from two commits",
              mentions(verify_mod.verify(d), "built from different kitchen commits"), True)
    with Fixture() as d:
        idx = json.load(open(os.path.join(d, "release-index.json")))
        idx = {"schema": "slax-kitchen/release-index/v1", "image": idx["images"][0],
               "assets": idx["assets"]}
        json.dump(idx, open(os.path.join(d, "release-index.json"), "w"))
        write_sums(d)
        check("a v1 index is refused, saying what it wants",
              mentions(verify_mod.verify(d), "not the slax-kitchen/release-index/v2"), True)


def test_the_claim_names_every_image():
    """redistribution-claim.py read index["image"] and the first sources manifest it found:
    given two images, it described the first alone and left the second's manifest out."""
    with Fixture(names=("x", "y")) as d:
        text = claim_mod.claim(d)
        check("both images, each with its hash", all(
            f"`{n}.iso`, sha256" in text for n in ("x", "y")), True)
        check("both manifests", all(f"`{n}.SOURCES.md` and `{n}.sources.json`" in text
                                    for n in ("x", "y")), True)
        check("records only: said once", text.count("No image is attached"), 1)
    with Fixture(names=("x", "y"), attached=True) as d:
        check("attached: says how many", "This release attaches 2 images" in claim_mod.claim(d),
              True)


def main():
    # EVERY FIXTURE THIS FILE MAKES GOES IN ONE BOX, AND THE BOX GOES AWAY.
    # 1 of this file's 2 mkdtemp() calls had no cleanup on 2026-09-18, so running it by hand left
    # their trees in /tmp and nothing took them away. ci/checks/80-unit.sh does this
    # for a GATE run; the box is the half that survives running the file directly.
    # Issue #25.
    #
    # Set before the first test, because tempfile.tempdir only steers calls made
    # after it -- and cleared afterwards so a caller that imports this file is not
    # left pointing at a directory that no longer exists.
    #
    # BOTH THE GLOBAL AND THE VARIABLE. tempfile.tempdir steers this process; TMPDIR steers
    # the children, and they are not the same thing. ci/release-assets.sh and three siblings
    # do `mktemp -d "${TMPDIR:-/tmp}/..."`, which reads the variable and never the global, so
    # the global on its own leaves a subprocess's fixture outside the box. Nothing here drives
    # one of those today -- but the box claims every fixture this file makes, and those are
    # fixtures this file made. Under the gate it changes nothing, since the ambient TMPDIR is
    # already the gate's own box; it is the by-hand run that this is for.
    box = tempfile.mkdtemp(prefix="test_release_assets-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_a_source_tar_is_named_the_way_every_other_asset_is,
                   test_nothing_may_take_the_name_of_a_file_this_script_writes,
                   test_a_fetched_file_the_sources_document_says_nothing_about,
                   test_a_complete_directory_passes,
                   test_sha256sums_must_be_exact,
                   test_what_the_manifest_refuses_is_refused,
                   test_built_here_needs_its_source,
                   test_the_project_archive_carries_its_submodules,
                   test_no_images_is_checked_by_content_not_by_name,
                   test_anything_that_is_not_a_regular_file_is_refused,
                   test_the_content_scan_runs_even_when_the_records_are_broken,
                   test_no_host_paths_and_no_renamed_names,
                   test_an_attached_image_carries_its_firmware_licenses,
                   test_the_claim_names_what_is_attached,
                   test_the_claim_says_only_what_the_directory_shows,
                   test_release_notes_use_the_claim_when_given_assets,
                   test_several_images_built_from_one_commit_are_one_set,
                   test_images_from_different_commits_are_not_one_release,
                   test_a_source_two_images_fetched_differently_is_refused,
                   test_each_image_in_a_set_is_held_to_its_own_records,
                   test_the_claim_names_every_image]:
            # One test crashing must not stop the rest: the count of failures is only honest
            # if every test ran. The traceback still goes to stderr, because a crash's location
            # is the useful half and a one-line summary loses it.
            try:
                fn()
            except Exception as e:                 # noqa: BLE001
                traceback.print_exc()
                FAILURES.append(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_release_assets.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
