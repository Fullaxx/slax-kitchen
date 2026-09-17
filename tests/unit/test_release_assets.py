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


def build(d, *, attached=False, submodule_files=True, firmware=None, extra=None,
          prov_extra=None, src_extra=None):
    """Write a passing assets directory into d, with optional breakage."""
    kit = "slax-kitchen-aaaaaaaaaaaa-source.tar.gz"
    with tarfile.open(os.path.join(d, kit), "w:gz") as t:
        tar_add(t, "slax-kitchen/README.md", b"readme\n")
        if submodule_files:
            tar_add(t, "slax-kitchen/vendor/linux-live/build", b"#!/bin/sh\n")
        tar_add(t, "slax-kitchen/SUBMODULES.txt", f"{LIVEKIT} vendor/linux-live\n".encode())
    grub = "grub2-unsigned_2.12-1ubuntu7.3.source.tar"
    with tarfile.open(os.path.join(d, grub), "w") as t:
        tar_add(t, "grub2-unsigned_2.12-1ubuntu7.3/grub2-unsigned_2.12-1ubuntu7.3.dsc", b"Format: 3.0\n")
    prov = {"schema": "slax-kitchen/provenance/v1",
            "kitchen": {"commit": COMMIT, "describe": "aaaaaaa", "dirty": False,
                        "submodules": {"vendor/linux-live": LIVEKIT}},
            "pack": {"iso": {"name": "x.iso", "sha256": IMG, "size": 3}}}
    prov.update(prov_extra or {})
    src = {"schema": "slax-kitchen/sources/v1", "image": {"name": "x.iso", "sha256": IMG},
           "components": [{"path": "boot/efi.img", "class": "built", "what": "GRUB EFI image"},
                          {"path": "slax/modules/01-core.sb", "class": "slax"}],
           "unresolved": [], "not_redistributable": [], "warnings": [],
           "firmware": firmware or {"stock_bundle": False, "license_texts": []}}
    src.update(src_extra or {})
    json.dump(prov, open(os.path.join(d, "x.iso.provenance.json"), "w"))
    json.dump(src, open(os.path.join(d, "x.sources.json"), "w"))
    open(os.path.join(d, "x.SOURCES.md"), "w").write("# Sources\n")
    roles = {kit: ("source", ["(kitchen)"], ["slax-kitchen at the recorded commit, with submodules"]),
             grub: ("source", ["boot/efi.img"], ["source of GRUB EFI image"]),
             "x.iso.provenance.json": ("provenance", None, None),
             "x.sources.json": ("sources", None, None),
             "x.SOURCES.md": ("sources", None, None)}
    if attached:
        open(os.path.join(d, "x.iso"), "wb").write(b"iso")
        roles["x.iso"] = ("image", None, None)
    for name, (content, role) in (extra or {}).items():
        open(os.path.join(d, name), "wb").write(content)
        roles[name] = (role, None, None)
    assets = []
    for name, (role, covers, what) in sorted(roles.items()):
        a = {"name": name, "role": role, "sha256": sha(os.path.join(d, name)),
             "size": os.path.getsize(os.path.join(d, name))}
        if covers:
            a["covers"] = covers
        if what:
            a["what"] = what
        assets.append(a)
    image_sha = sha(os.path.join(d, "x.iso")) if attached else IMG
    if attached:
        # An attached image has to be the one the records describe.
        for doc_name in ("x.iso.provenance.json", "x.sources.json"):
            doc = json.load(open(os.path.join(d, doc_name)))
            (doc.get("pack") or doc)["iso" if "pack" in doc else "image"]["sha256"] = image_sha
            json.dump(doc, open(os.path.join(d, doc_name), "w"))
        for a in assets:
            a["sha256"] = sha(os.path.join(d, a["name"]))
    json.dump({"schema": "slax-kitchen/release-index/v1",
               "image": {"name": "x.iso", "sha256": image_sha, "attached": attached},
               "assets": assets}, open(os.path.join(d, "release-index.json"), "w"))
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


def test_no_host_paths_and_no_renamed_names():
    with Fixture(prov_extra={"note": "/home/someone/build"}) as d:
        check("a host path", mentions(verify_mod.verify(d), "path on the build machine"), True)
    with Fixture(extra={"a~b.txt": (b"x", "source")}) as d:
        check("a name GitHub renames", mentions(verify_mod.verify(d), "a~b.txt: GitHub would rename"), True)


def test_an_attached_image_carries_its_firmware_licenses():
    fw = {"stock_bundle": True, "license_texts": []}
    with Fixture(attached=True, firmware=fw) as d:
        check("stock firmware alone", mentions(verify_mod.verify(d), "without the firmware's license"), True)
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
        check("no license-text claim without the texts", "license texts are inside" in text, False)
        check("says what is true instead", "removed the license texts" in text, True)
        check("no identity claim it cannot check", "official Slax release" in text, False)
    restored = {"stock_bundle": True, "license_texts": [{"recipe": "firmware-refresh",
                                                         "bundle": "slax/modules/09-firmware-debian.sb",
                                                         "packages": ["firmware-iwlwifi"]}]}
    with Fixture(firmware=restored) as d:
        check("the texts, when they are there", "license texts are inside" in claim_mod.claim(d), True)


def test_release_notes_use_the_claim_when_given_assets():
    with Fixture(attached=True) as d:
        p = subprocess.run([os.path.join(ROOT, "ci", "release-notes.sh"), "v9.9.9"], cwd=ROOT,
                           capture_output=True, text=True,
                           env=dict(os.environ, REPO_ROOT=ROOT, RELEASE_ASSETS=d))
        check("renders", p.returncode, 0)
        check("the claim is in the notes", "This release attaches `x.iso`" in p.stdout, True)
        check("and the toolkit sentence is not", "a release of slax-kitchen is the toolkit" in p.stdout,
              False)


def main():
    for fn in [test_a_complete_directory_passes,
               test_sha256sums_must_be_exact,
               test_what_the_manifest_refuses_is_refused,
               test_built_here_needs_its_source,
               test_the_project_archive_carries_its_submodules,
               test_no_images_is_checked_by_content_not_by_name,
               test_no_host_paths_and_no_renamed_names,
               test_an_attached_image_carries_its_firmware_licenses,
               test_the_claim_names_what_is_attached,
               test_the_claim_says_only_what_the_directory_shows,
               test_release_notes_use_the_claim_when_given_assets]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_release_assets.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
