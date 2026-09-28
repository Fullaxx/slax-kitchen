#!/usr/bin/env python3
"""Unit tests for `kitchen sources`' classification, which is pure and needs no ISO.

Each case is a shape of image the classifier has to get right, most of them seen for real
while the command was written: a repacked isolinux.bin, a menu edit nobody recorded, a
bundle altered after pack, a script that left a binary behind. Since #62 no class is an
error: the cases that used to be refusals pin the neutral listing instead, and #60's case --
a staged input holding compiled code -- is a part of its recipe's bundle.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))

import sources  # noqa: E402
import traceback

FAILURES = []
H = {c: c * 64 for c in "abcdef0123456789"}


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


STOCK = {"files": {"slax/modules/01-core.sb": H["a"], "slax/boot/isolinux.cfg": H["b"],
                   "readme.txt": H["c"], "slax/boot/isolinux.bin": H["d"],
                   "slax/boot/isolinux.bin@64": H["e"]},
         "initramfs": {}}


def prov(recipes=(), dirty=False, builder_id="ubuntu", **extra):
    doc = {"base": {"name": "slax-64bit-debian-12.2.0.iso", "sha256": H["f"]},
           "kitchen": {"commit": H["0"][:40], "describe": "abc1234" + ("-dirty" if dirty else ""),
                       "dirty": dirty},
           "builder": {"id": builder_id},
           "recipes": list(recipes), "pack": {"iso": {"name": "x.iso"}}}
    doc.update(extra)
    return doc


def run(files, p, **kw):
    return sources.classify(files, STOCK, p, "debian-64bit-12.2.0", {}, **kw)


def classes(doc):
    return {c["path"]: c["class"] for c in doc["components"]}


def test_stock_files_are_slax_by_hash():
    doc = run({"slax/modules/01-core.sb": H["a"], "readme.txt": H["c"]}, prov())
    check("stock bundle", classes(doc)["slax/modules/01-core.sb"], "slax")
    check("nothing unrecorded", doc["summary"]["unrecorded"], 0)


def test_a_renumbered_stock_bundle_is_still_slax():
    """`renumber-bundles` renames a stock bundle: same bytes, new name. Matched by path
    alone it fell through to the recipe's artifact list and was reported as ours -- Slax's
    binary bundle, claimed as project source, in the document whose whole job is
    attribution."""
    r = {"recipe": "renumber-bundles", "steps": [{"verb": "bundle.renumber"}],
         "artifacts": ["slax/modules/95-core.sb"]}
    doc = run({"slax/modules/95-core.sb": H["a"]}, prov([r]))
    comp = doc["components"][0]
    check("still Slax as published", comp["class"], "slax")
    check("and says where it came from", "01-core.sb" in (comp.get("note") or ""), True)
    check("nothing unrecorded", doc["summary"]["unrecorded"], 0)


def test_a_changed_stock_file_with_no_record_is_unrecorded():
    doc = run({"slax/boot/isolinux.cfg": H["9"]}, prov())
    comp = doc["components"][0]
    check("unrecorded", comp["class"], "unrecorded")
    check("says why", "differs from the stock image" in comp.get("note", ""), True)


def test_isolinux_bin_is_recognised_after_the_boot_info_table_is_rewritten():
    """Every mastering run rewrites bytes 8-63; the rest must still identify it."""
    doc = run({"slax/boot/isolinux.bin": H["9"], "slax/boot/isolinux.bin@64": H["e"]}, prov())
    check("still slax", classes(doc).get("slax/boot/isolinux.bin"), "slax")
    doc = run({"slax/boot/isolinux.bin": H["9"], "slax/boot/isolinux.bin@64": H["8"]}, prov())
    check("a changed tail is not", classes(doc).get("slax/boot/isolinux.bin"), "unrecorded")


def _tarball_recipe(sha, upstream="https://example.org/src/"):
    step = {"verb": "bundle.fromTarball", "output": "slax/modules/15-app.sb", "output_sha256": sha,
            "source": "https://example.org/app.tar.xz", "source_sha256": H["2"]}
    if upstream:
        step["upstream_source"] = upstream
    return {"recipe": "app", "steps": [step], "artifacts": ["slax/modules/15-app.sb"]}


def test_a_recorded_download_is_prebuilt_only_when_the_bytes_match():
    doc = run({"slax/modules/15-app.sb": H["3"]}, prov([_tarball_recipe(H["3"])]))
    check("prebuilt", classes(doc)["slax/modules/15-app.sb"], "prebuilt")
    check("carries its upstream", doc["components"][0]["upstream_source"], "https://example.org/src/")
    # Altered after the step ran: the recorded sha256 no longer matches, and the journal's
    # artifact list (which names the same path) must not vouch for it instead.
    doc = run({"slax/modules/15-app.sb": H["4"]}, prov([_tarball_recipe(H["3"])]))
    comp = doc["components"][0]
    check("altered bundle is not attributed to its step", comp["class"], "unrecorded")
    check("says it changed", "changed after" in comp.get("note", ""), True)
    # A LATER recipe that edits the file is a legitimate writer: that recipe's.
    later = {"recipe": "edit-later", "steps": [], "artifacts": ["slax/modules/15-app.sb"]}
    doc = run({"slax/modules/15-app.sb": H["4"]}, prov([_tarball_recipe(H["3"]), later]))
    check("later edit is the recipe's", [(c["class"], c["by"]) for c in doc["components"]],
          [("recipe", "edit-later")])


def newc(entries):
    """A `newc` cpio archive, built byte by byte. entries: (name, mode, data, ino, nlink)."""
    out = b""
    for name, mode, data, ino, nlink in entries + [("TRAILER!!!", 0, b"", 0, 1)]:
        raw = name.encode() + b"\0"
        fields = [ino, mode, 0, 0, nlink, 0, len(data), 0, 0, 0, 0, len(raw), 0]
        head = b"070701" + b"".join(b"%08X" % f for f in fields) + raw
        head += b"\0" * (-len(head) % 4)
        out += head + data + b"\0" * (-len(data) % 4)
    return out


def test_the_cpio_reader_reads_what_cpio_would():
    """The initramfs is parsed here rather than unpacked, so the header walk is the thing
    to get wrong: field offsets, the two paddings, and hard links -- newc stores a hard
    link's data ONCE, with the last name, and gives every earlier name filesize 0. Hashing
    those as empty would report them as members that no longer match Slax."""
    import hashlib
    body = b"#!/bin/sh\nexec /init\n"
    h = hashlib.sha256(body).hexdigest()
    arc = newc([("init", 0o100755, body, 11, 1),
                ("dev/console", 0o020600, b"", 12, 1),          # a device node: not a file
                ("bin/ln1", 0o100755, b"", 13, 2),              # hard link, data comes later
                ("etc/passwd", 0o100644, b"root:x:0:0:\n", 14, 1),
                ("bin/ln2", 0o100755, body, 13, 2)])
    got = sources.cpio_members(arc)
    check("device nodes are not members", "dev/console" in got, False)
    check("every regular file is", sorted(got), ["bin/ln1", "bin/ln2", "etc/passwd", "init"])
    check("content read correctly", got["init"], h)
    check("and both names of a hard link carry it", (got["bin/ln1"], got["bin/ln2"]), (h, h))
    try:                                        # a cut-off archive is refused, not summarised
        sources.cpio_members(arc[:60])
        check("a truncated archive is refused", "returned", "raised ValueError")
    except ValueError:
        pass


def test_a_broken_archive_is_reported_as_unreadable_not_as_a_traceback():
    """`kitchen sources` runs against images the operator did not build, so the parser is
    fed hostile bytes by definition. Three ways it went wrong: a header whose fields are
    not hex raised ValueError out of the tool; int() accepts a sign, so a negative
    namesize made the offset stand still and the loop never ended; and a truncated tail
    hashed short data, which reported a real member as changed -- a wrong claim, which is
    worse than an unreadable one."""
    import signal

    def within(seconds, fn, *a):
        """Run fn, failing the test rather than hanging the suite."""
        def boom(_s, _f):
            raise AssertionError("cpio_members did not finish")
        old_handler = signal.signal(signal.SIGALRM, boom)
        signal.alarm(seconds)
        try:
            return fn(*a)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

    def refused(blob, what):
        try:
            within(5, sources.cpio_members, blob)
        except ValueError:
            return True
        except AssertionError as e:
            check(what, str(e), "(finished)")
            return False
        return False

    check("fields that are not hex", refused(b"070701" + b"Z" * 104 + b"x\0", "hex"), True)
    # A negative namesize: int() would take it, and data_at would land back on off.
    neg = b"070701" + b"%08X" % 1 + b"%08X" % 0o100644 + b"%08X" % 0 * 9 + b"-000006e" + b"%08X" % 0
    check("a signed field", refused(neg, "signed"), True)
    good = newc([("init", 0o100755, b"hello\n", 1, 1)])
    check("a truncated archive", refused(good[:len(good) - 40], "truncated"), True)
    check("one with no trailer", refused(good[:len(good) - 120], "no trailer"), True)
    check("and a sound one still reads", sorted(sources.cpio_members(good)), ["init"])


def test_an_initramfs_that_decompresses_to_more_than_the_cap_is_refused():
    """`kitchen sources` reads images the operator did not build. xz packs zeroes about
    1000:1, so an initrfs.img of a few MiB can ask for tens of GiB and the tool is killed
    by the OOM killer instead of reporting what it found."""
    import lzma
    bomb = lzma.compress(b"\0" * (2 << 20))
    check("a small bomb, with the cap lowered to catch it in a test",
          with_cap(1 << 20, sources.initramfs_from_bytes, bomb), None)
    good = lzma.compress(newc([("init", 0o100755, b"hi\n", 1, 1)]))
    check("and an ordinary initramfs still reads",
          sorted(sources.initramfs_from_bytes(good) or {}), ["init"])


def with_cap(cap, fn, *a):
    old = sources.INITRAMFS_MAX
    sources.INITRAMFS_MAX = cap
    try:
        return fn(*a)
    finally:
        sources.INITRAMFS_MAX = old


def test_an_image_with_no_manifest_is_not_called_unreadable():
    """`initramfs_delta` collapsed "could not be read" and "this target has no manifest"
    into one answer, and the note said the initramfs could not be unpacked -- about an
    image that unpacked perfectly, when what was missing was the kitchen's own inventory."""
    unreadable = sources.initramfs_delta(None, {"init": H["1"]})
    nothing_to_compare = sources.initramfs_delta({"init": H["1"]}, {})
    check("both say nothing was compared",
          (unreadable["compared"], nothing_to_compare["compared"]), (False, False))
    check("but for different reasons", unreadable["why"] == nothing_to_compare["why"], False)
    check("the first is about the image", "could not be read" in unreadable["why"], True)
    check("the second about the manifest", "manifest" in nothing_to_compare["why"], True)


def test_a_script_that_had_network_says_so():
    """`network: true` was recorded on the step and read by nothing. It changes no class --
    fetched files and packages account for themselves -- but nothing here can see a
    download a script made without the KITCHEN-FETCHED protocol, so the record has to say
    whose account this is."""
    step = {"verb": "bundle.script", "output": "slax/modules/09-fw.sb", "output_sha256": H["2"],
            "network": True, "upstream_source": "https://git.kernel.org/linux-firmware",
            "fetched": [{"path": "usr/lib/firmware/x.bin", "sha256": H["3"]}]}
    doc = run({"slax/modules/09-fw.sb": H["2"]}, prov([{"recipe": "fw", "steps": [step]}]))
    comp = [c for c in doc["components"] if c["path"] == "slax/modules/09-fw.sb"][0]
    check("recorded", comp["network"], True)
    check("and said in the note", "script's own account" in comp["note"], True)

    step = dict(step, network=None)
    doc = run({"slax/modules/09-fw.sb": H["2"]}, prov([{"recipe": "fw", "steps": [step]}]))
    comp = [c for c in doc["components"] if c["path"] == "slax/modules/09-fw.sb"][0]
    check("a script with no network does not say it", "own account" in comp["note"], False)


def test_the_initramfs_note_counts_members_instead_of_asserting_them():
    """The note said "members that match the stock initramfs manifest are Slax as
    published" while nothing compared any member: the manifest was read into `stock` and
    never looked at again."""
    stock = {"bin/busybox": H["1"], "init": H["2"], "bin/blkid": H["3"]}
    got = sources.initramfs_delta({"bin/busybox": H["4"], "init": H["2"], "bin/blkid": H["3"]}, stock)
    check("counted", (got["members"], got["stock_members"]), (3, 2))
    check("named what changed", got["changed"], ["bin/busybox"])
    check("nothing lost", got["removed"], [])
    gone = sources.initramfs_delta({"init": H["2"]}, stock)
    check("a dropped member", gone["removed"], ["bin/blkid", "bin/busybox"])
    quiet = sources.initramfs_delta(None, stock)
    check("unreadable initramfs claims nothing", quiet["compared"], False)


def test_an_unpinned_download_is_said_out_loud():
    """bundle.fromTarball records `pinned`, and nothing read it: an archive taken on trust
    (whatever the server served that day) was rendered exactly like a sha256-pinned one.
    Since #62 pinning is the recipe's choice and nothing refuses an unpinned download, so
    the report is the one place it is said."""
    step = {"verb": "bundle.fromTarball", "output": "slax/modules/15-app.sb", "output_sha256": H["3"],
            "source": "https://example.org/app.tar.xz", "upstream_source": "https://example.org/src/",
            "pinned": False}
    doc = run({"slax/modules/15-app.sb": H["3"]}, prov([{"recipe": "app", "steps": [step]}]))
    check("listed, not refused", (doc["components"][0]["class"], doc["summary"]["unrecorded"]),
          ("prebuilt", 0))
    check("and said in SOURCES.md", "no sha256 was pinned" in sources.markdown(doc), True)
    step["pinned"] = True
    doc = run({"slax/modules/15-app.sb": H["3"]}, prov([{"recipe": "app", "steps": [step]}]))
    check("a pinned one is not", "no sha256 was pinned" in sources.markdown(doc), False)


def test_a_mirror_path_cannot_impersonate_debian():
    """The origin is an apt list filename with / turned into _, so `debian.org` can appear
    in the PATH of some other host: mirror.example.com_debian.org_pub_... . Matching the
    whole string handed that package a snapshot.debian.org URL that never had it."""
    origin = "mirror.example.com_debian.org_pub_dists_bookworm_main_binary-amd64_Packages"
    step = {"verb": "bundle.packages", "flavour": "debian", "output": "slax/modules/12-x.sb",
            "output_sha256": H["5"],
            "installed": [{"package": "evil", "version": "1", "architecture": "amd64"}],
            "debs": [{"package": "evil", "version": "1", "architecture": "amd64",
                      "sha256": H["6"], "origin": origin}]}
    doc = run({"slax/modules/12-x.sb": H["5"]}, prov([{"recipe": "x", "steps": [step]}]))
    comp = doc["components"][0]
    check("not given a Debian pointer", (comp["packages"][0]["archive"],
                                         comp["packages"][0]["source_published_at"]), (origin, None))
    check("and the report says why", any("does not declare" in n for n in comp.get("notes") or []),
          True)


def test_packages_point_at_snapshot_including_reinstalls():
    step = {"verb": "bundle.packages", "flavour": "debian", "output": "slax/modules/09-fw.sb",
            "output_sha256": H["5"], "reinstall": True,
            "installed": [{"package": "acpid", "version": "1:2.0.33-2", "source": "acpid",
                           "source_version": "1:2.0.33-2"}],
            "debs": [{"file": "firmware-iwlwifi_20230210-5_all.deb", "sha256": H["6"],
                      "package": "firmware-iwlwifi", "version": "20230210-5",
                      "source": "firmware-nonfree", "source_version": "20230210-5"}]}
    doc = run({"slax/modules/09-fw.sb": H["5"]}, prov([{"recipe": "fw", "steps": [step]}]))
    comp = doc["components"][0]
    check("debian", comp["class"], "debian")
    check("both packages", [p["package"] for p in comp["packages"]], ["acpid", "firmware-iwlwifi"])
    check("epoch encoded", comp["packages"][0]["source_published_at"],
          "https://snapshot.debian.org/package/acpid/1%3A2.0.33-2/")
    check("reinstall found through its .deb", comp["packages"][1]["source"], "firmware-nonfree")
    # Debian leaves Source: out when it would repeat the binary's name and version.
    step["installed"] = [{"package": "acpid", "version": "1:2.0.33-2"}]
    doc = run({"slax/modules/09-fw.sb": H["5"]}, prov([{"recipe": "fw", "steps": [step]}]))
    check("no Source: means itself", doc["components"][0]["packages"][0]["source_published_at"],
          "https://snapshot.debian.org/package/acpid/1%3A2.0.33-2/")
    step["installed"] = [{"package": "acpid"}]
    doc = run({"slax/modules/09-fw.sb": H["5"]}, prov([{"recipe": "fw", "steps": [step]}]))
    comp = doc["components"][0]
    check("no version at all: listed with no pointer, not a crash",
          (comp["class"], comp["packages"][0].get("source_published_at")), ("debian", None))
    check("and said", any("no source version" in n for n in comp.get("notes") or []), True)


WINE_LIST = "dl.winehq.org_wine-builds_debian_dists_bookworm_main_binary-amd64_Packages"
DEBIAN_LIST = "deb.debian.org_debian_dists_bookworm_main_binary-amd64_Packages"


def _repo_step(origin, upstream="https://gitlab.winehq.org/wine/wine", with_repo=True):
    step = {"verb": "bundle.packages", "flavour": "debian", "output": "slax/modules/12-wine.sb",
            "output_sha256": H["5"],
            "installed": [{"package": "winehq-stable", "version": "9.0.0.0~bookworm-1",
                           "architecture": "amd64"},
                          {"package": "libfaudio0", "version": "23.01-1",
                           "architecture": "amd64", "source": "faudio"}],
            "debs": [{"package": "winehq-stable", "version": "9.0.0.0~bookworm-1",
                      "architecture": "amd64", "sha256": H["6"], "origin": origin},
                     {"package": "libfaudio0", "version": "23.01-1", "architecture": "amd64",
                      "sha256": H["7"], "origin": DEBIAN_LIST}]}
    if with_repo:
        step["apt_sources"] = [{"name": "winehq", "uri": "https://dl.winehq.org/wine-builds/debian/",
                                "upstream_source": upstream}]
    return {"recipe": "wine", "steps": [step]}


def test_a_package_from_a_recipe_repository_points_at_that_repository():
    """snapshot.debian.org never had WineHQ's packages; a link there would be a 404."""
    doc = run({"slax/modules/12-wine.sb": H["5"]}, prov([_repo_step(WINE_LIST)]))
    pk = {p["package"]: p for p in doc["components"][0]["packages"]}
    check("wine from its repository", (pk["winehq-stable"]["archive"],
                                       pk["winehq-stable"]["source_published_at"]),
          ("winehq", "https://gitlab.winehq.org/wine/wine"))
    check("its dependency from Debian", pk["libfaudio0"]["source_published_at"],
          "https://snapshot.debian.org/package/faudio/23.01-1/")
    md = sources.markdown(doc)
    check("not listed as a Debian source package", "`winehq-stable` `9.0.0.0~bookworm-1`, from `winehq`"
          in md and "snapshot.debian.org/package/winehq-stable" not in md, True)


def test_a_package_from_an_undeclared_archive_has_no_pointer():
    doc = run({"slax/modules/12-wine.sb": H["5"]},
              prov([_repo_step("ppa.example.org_x_dists_bookworm_main_binary-amd64_Packages")]))
    notes = doc["components"][0].get("notes") or []
    check("undeclared archive", any("does not declare" in n for n in notes), True)
    r = _repo_step(None)
    for d in r["steps"][0]["debs"]:
        d.pop("origin", None)
    doc = run({"slax/modules/12-wine.sb": H["5"]}, prov([r]))
    check("unknown origin with repositories in play, for each package",
          sum("no record of which archive" in n for n in doc["components"][0].get("notes") or []), 2)
    doc = run({"slax/modules/12-wine.sb": H["5"]}, prov([_repo_step(None, with_repo=False)]))
    check("no repositories: Debian's archive is the only one", doc["components"][0].get("notes"),
          None)


def test_slackware_packages_point_at_the_release_source_tree():
    step = {"verb": "bundle.script", "output": "slax/modules/07-slackpkgs.sb", "output_sha256": H["5"],
            "slackware_installed": ["nano-6.0-x86_64-1", "rsync-3.2.3-x86_64-4"],
            "upstream_source": "https://mirrors.slackware.com/slackware/slackware-15.0/source/"}
    doc = run({"slax/modules/07-slackpkgs.sb": H["5"]}, prov([{"recipe": "txz", "steps": [step]}]))
    pk = doc["components"][0]["packages"]
    check("pointed at the tree", {p["source_published_at"] for p in pk},
          {"https://mirrors.slackware.com/slackware/slackware-15.0/source/"})
    check("listed in the manifest", "`nano-6.0-x86_64-1`" in sources.markdown(doc), True)
    del step["upstream_source"]
    doc = run({"slax/modules/07-slackpkgs.sb": H["5"]}, prov([{"recipe": "txz", "steps": [step]}]))
    check("no tree named: said, with no pointer",
          any("gives no upstream_source" in n for n in doc["components"][0].get("notes") or []), True)


def test_apt_names_index_files_the_way_apt_does():
    check("plain", sources.apt_list_prefix("https://dl.winehq.org/wine-builds/debian/"),
          "dl.winehq.org_wine-builds_debian")
    check("quoted and ported", sources.apt_list_prefix("http://example.org:8080/a_b~c"),
          "example.org:8080_a%5fb%7ec")


def _script_recipe(elf=True, declared=False):
    step = {"verb": "bundle.script", "output": "slax/modules/07-tool.sb", "output_sha256": H["7"]}
    if elf:
        step["unowned_elf"] = [{"path": "usr/local/bin/tool", "sha256": H["8"]}]
    if declared:
        step["declares"] = [{"path": "usr/local/bin/tool", "source_url": "https://example.org/t.tar",
                             "source_sha256": H["9"]}]
    return {"recipe": "tool", "steps": [step]}


def test_a_binary_a_script_left_is_part_of_its_bundle():
    """An ELF file a script left that no package vouched for made the whole bundle
    unresolved; `declares:` was the way out. Since #62 the bundle is the recipe's whatever
    the script left in it, and an older image's record of such files changes nothing. A
    `declares:` entry still says what the script compiled: a built part, with its source."""
    doc = run({"slax/modules/07-tool.sb": H["7"]}, prov([_script_recipe()]))
    check("the recipe's bundle", (doc["components"][0]["class"], doc["summary"]["unrecorded"]),
          ("recipe", 0))
    doc = run({"slax/modules/07-tool.sb": H["7"]}, prov([_script_recipe(declared=True)]))
    check("a declared binary is a built part", doc["components"][0]["parts"][0]["class"], "built")


def test_busybox_is_built_and_an_unverified_claim_is_said():
    def rec(verified):
        return {"recipe": "initramfs-busybox", "steps": [{
            "verb": "initramfs.busybox", "output": "slax/boot/initrfs.img", "output_sha256": H["1"],
            "claim": {"verified": verified, "claim": {"sources": [{"url": "https://busybox.net/x",
                                                                  "sha256": H["2"]}]}}}]}
    doc = run({"slax/boot/initrfs.img": H["1"]}, prov([rec(False)]))
    part = doc["components"][0]["parts"][0]
    check("an unverified claim is still a built part", (part["class"], doc["summary"]["unrecorded"]),
          ("built", 0))
    check("said, with nothing to point at", (part.get("note"), part.get("sources")),
          ("its build claim does not match the binary", None))
    doc = run({"slax/boot/initrfs.img": H["1"]}, prov([rec(True)]))
    check("a verified claim points at its source", doc["components"][0]["parts"][0].get("sources"),
          [{"url": "https://busybox.net/x", "sha256": H["2"]}])
    r = rec(True)
    r["steps"][0]["claim"]["claim"].update(
        container={"alpine_release": "3.19.9"},
        linked=[{"name": "musl", "version": "1.2.4_git20230717-r6", "license": "MIT"}])
    doc = run({"slax/boot/initrfs.img": H["1"]}, prov([r]))
    check("linked libc points at the stable branch, not the tag",
          doc["components"][0]["parts"][0]["linked"][0]["source_published_at"],
          "https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.19-stable/main/musl")
    check("and the manifest says it is linked in",
          "statically linked into `bin/busybox`" in sources.markdown(doc), True)


def test_grub_is_built_and_its_source_follows_the_builder():
    def rec(tools):
        return {"recipe": "uefi-bootable", "steps": [{
            "verb": "boot.uefi", "output": "boot/efi.img", "output_sha256": H["3"], "tools": tools}]}
    grub = {"grub-efi-amd64-bin": {"package": "grub-efi-amd64-bin", "source": "grub2-unsigned",
                                   "source_version": "2.12-1ubuntu7.3"}}
    doc = run({"boot/efi.img": H["3"]}, prov([rec(grub)]))
    check("built", classes(doc)["boot/efi.img"], "built")
    check("launchpad on ubuntu", doc["components"][0]["source_package"]["published_at"],
          "https://launchpad.net/ubuntu/+source/grub2-unsigned/2.12-1ubuntu7.3")
    doc = run({"boot/efi.img": H["3"]}, prov([rec(grub)], builder_id="debian"))
    check("snapshot on debian", doc["components"][0]["source_package"]["published_at"],
          "https://snapshot.debian.org/package/grub2-unsigned/2.12-1ubuntu7.3/")
    doc = run({"boot/efi.img": H["3"]}, prov([rec({})]))
    comp = doc["components"][0]
    check("no host record: built, with no pointer, and said",
          (comp["class"], comp.get("source_package"), comp.get("note")),
          ("built", None, "the build host recorded no GRUB package"))
    # dpkg-query -S found the owner but -W failed: a package name and nothing to point at.
    # That used to pass as `built` with a null source and a null URL.
    doc = run({"boot/efi.img": H["3"]},
              prov([rec({"grub-efi-amd64-bin": {"package": "grub-efi-amd64-bin"}})]))
    comp = doc["components"][0]
    check("a package with no source version has no pointer", comp.get("source_package"), None)
    check("and says so", "source version" in comp.get("note", ""), True)


def test_recipe_edits_and_pack_output_are_this_builds():
    p = prov([{"recipe": "serial-console", "steps": [], "artifacts": ["slax/boot/isolinux.cfg"]}],
             pack={"iso": {}, "generated": [{"path": "slax/modules/98-dpkg-db.sb", "sha256": H["4"]}]})
    doc = run({"slax/boot/isolinux.cfg": H["9"], "slax/modules/98-dpkg-db.sb": H["4"]}, p)
    check("edit is the recipe's", classes(doc)["slax/boot/isolinux.cfg"], "recipe")
    check("generated db is this build's", classes(doc)["slax/modules/98-dpkg-db.sb"], "recipe")
    check("nothing unrecorded", doc["summary"]["unrecorded"], 0)


def test_a_dirty_build_is_a_note_not_a_refusal():
    """A kitchen checkout with uncommitted changes made the image unresolved unless
    --allow-dirty (#53), and slax-wine's images are all built `-dirty` at a pin bump. What
    it means for the report -- the recorded commit does not hold every change -- is a note,
    for the project's checkout as well as the kitchen's (#62)."""
    doc = run({}, prov(dirty=True, project={"commit": H["1"][:40], "describe": "13dc2db-dirty",
                                            "dirty": True}))
    check("a note for each checkout", [n.split(" checkout")[0] for n in doc["notes"]],
          ["the kitchen", "the project"])
    check("naming its describe", "13dc2db-dirty" in doc["notes"][1], True)
    check("and nothing unrecorded", doc["summary"]["unrecorded"], 0)
    check("a clean build has none", run({}, prov())["notes"], [])


def test_an_image_built_on_another_image_lists_its_files_as_base():
    """LAYERING.md: a project builds on another project's image, as slax-rpgs is to on
    slax-wine's, and that image is not a stock target. Every file from it was unresolved,
    the stock Slax ones included, so no such image could be published (#61, #62). A file
    no step of this build made came with the base image, and the report says so."""
    games = {"recipe": "games", "steps": [{"verb": "bundle.files",
                                           "output": "slax/modules/30-games.sb",
                                           "output_sha256": H["7"]}]}
    p = prov([games], base={"name": "slax64-wine-uefi-1.0.0.iso", "sha256": H["9"]})
    doc = sources.classify({"slax/modules/01-core.sb": H["a"], "slax/modules/21-wine.sb": H["b"],
                            "slax/modules/30-games.sb": H["7"]},
                           {"files": {}, "initramfs": {}}, p, None, {})
    check("the base's files are base, this build's the recipe's", classes(doc),
          {"slax/modules/01-core.sb": "base", "slax/modules/21-wine.sb": "base",
           "slax/modules/30-games.sb": "recipe"})
    check("nothing unrecorded", doc["summary"]["unrecorded"], 0)
    check("a note says why", "not a stock Slax release" in doc["notes"][0], True)
    check("and SOURCES.md points at the base image",
          "**The base image**, `slax64-wine-uefi-1.0.0.iso`" in sources.markdown(doc), True)


def test_a_sidecar_for_another_iso_is_a_note():
    """An ISO whose sha256 is not the one its provenance records was unresolved (#62): the
    image changed after pack, or the sidecar is another build's. The report may not describe
    it, and it says so first -- without refusing to be written."""
    p = {"pack": {"iso": {"sha256": H["1"]}}}
    check("a different ISO", "not the one its provenance records" in
          (sources.sidecar_note(p, H["2"]) or ""), True)
    check("the same ISO", sources.sidecar_note(p, H["1"]), None)
    check("an older sidecar that records none", sources.sidecar_note({}, H["2"]), None)


def test_the_mbr_is_a_prebuilt_host_component():
    p = prov(pack={"iso": {}, "mbr": {"file": "isohdpfx.bin", "sha256": H["5"], "package": {
        "package": "isolinux", "source": "syslinux", "source_version": "3:6.04-1"}}})
    comp = [c for c in run({}, p)["components"] if c["path"] == "(system area)"][0]
    check("prebuilt", comp["class"], "prebuilt")
    check("epoch encoded", comp["upstream_source"],
          "https://launchpad.net/ubuntu/+source/syslinux/3%3A6.04-1")


def _recipe_with_inputs(recipe_file, *inputs):
    return {"recipe": "overlay", "recipe_file": recipe_file,
            "steps": [{"verb": "rootcopy.files", "local_inputs": list(inputs)}]}


def test_what_a_recipe_copies_in_is_listed_whatever_it_holds():
    """#60 and #62: slax-wine's staged Notepad++ installers and its Flatpak tree -- inputs the
    recorded commit did not hold, the Flatpak with 4,473 ELF files -- left every one of its
    images unresolved, and --allow-dirty waived only half of that. What a recipe copies in is
    what it asked for: the bundle is the recipe's, the report names the input, and nothing
    is held to a commit or refused as compiled code. The fields an older kitchen recorded
    for that check are read by nothing."""
    staged = {"root": "project", "path": "recipes/available/bottles.files/var/lib/flatpak",
              "kind": "dir", "digest": H["1"], "in_archive": False, "elf": ["files/bin/7zz"]}
    step = {"verb": "bundle.files", "output": "slax/modules/30-bottles.sb", "output_sha256": H["7"],
            "local_inputs": [staged]}
    recipe = {"recipe": "bottles", "recipe_file": {"outside": "bottles.yaml", "kind": "file"},
              "steps": [step]}
    doc = run({"slax/modules/30-bottles.sb": H["7"]}, prov([recipe]))
    comp = doc["components"][0]
    check("the recipe's bundle, and nothing else to say",
          (comp["class"], doc["summary"]["unrecorded"], doc["notes"]), ("recipe", 0, []))
    check("naming what it copied in", comp.get("inputs"),
          ["recipes/available/bottles.files/var/lib/flatpak"])
    check("and so does SOURCES.md", "copied in: `recipes/available/bottles.files/var/lib/flatpak`"
          in sources.markdown(doc), True)


def test_a_file_from_a_build_host_package_points_at_that_package():
    """locale-timezone-keyboard copies the build host's tzfile into /etc/localtime: a
    recipe cannot reference a path inside the tree it is building. The host's tzdata owns
    it, which is a source pointer like the MBR's."""
    inside = {"root": "kitchen", "path": "recipes/available/l.yaml", "kind": "file"}
    tz = {"outside": "Prague", "kind": "file",
          "host_package": {"package": "tzdata", "source": "tzdata",
                           "source_version": "2024a-3ubuntu1.1"}}
    doc = run({}, prov([_recipe_with_inputs(inside, tz)]))
    check("pointed at the host package", doc["host_inputs"][0]["published_at"],
          "https://launchpad.net/ubuntu/+source/tzdata/2024a-3ubuntu1.1")
    check("and listed", "the build host's `tzdata`" in sources.markdown(doc), True)
    unowned = dict(tz, host_package=None)
    check("an unowned host file has nothing to point at, and is not an error",
          run({}, prov([_recipe_with_inputs(inside, unowned)]))["host_inputs"], [])
    tree = dict(tz, kind="dir")
    check("nor a host directory", run({}, prov([_recipe_with_inputs(inside, tree)]))["host_inputs"],
          [])


def test_markdown_renders():
    doc = run({"slax/modules/15-app.sb": H["3"]}, prov([_tarball_recipe(H["3"])]))
    md = sources.markdown(doc)
    check("has the summary table", "| prebuilt | 1 |" in md, True)


def _files_recipe(fetched, inputs=()):
    step = {"verb": "bundle.files", "output": "slax/modules/17-jq.sb", "output_sha256": H["7"]}
    if fetched is not None:
        step["fetched"] = fetched
    if inputs:
        step["local_inputs"] = list(inputs)
    return {"recipe": "bundle-from-url", "steps": [step]}


def test_a_file_bundle_files_downloaded_is_a_prebuilt_part_of_its_bundle():
    """A bundle.files `url:` entry is a download the engine fetched itself: a prebuilt part
    (#59).

    Before #59 a single download reached a bundle through bundle.script, whose record is
    what the script says it fetched. Now the engine fetches the file, and refuses it when
    the recipe pins a sha256 the file does not have, so the record is its own: the bundle
    is the recipe's, and the file a prebuilt part pointing upstream. Since #62 the pin and
    the pointer are both optional, and a staged `src:` beside it is named, not refused.
    """
    jq = {"path": "usr/local/bin/jq", "sha256": H["8"], "url": "https://example.org/jq",
          "upstream_source": "https://example.org/jq/source/", "pinned": True}
    doc = run({"slax/modules/17-jq.sb": H["7"]}, prov([_files_recipe([jq])]))
    comp = doc["components"][0]
    check("the bundle is the recipe's", (comp["class"], doc["summary"]["unrecorded"]),
          ("recipe", 0))
    check("the download is a prebuilt part pointing upstream", comp.get("parts"), [
        {"member": "usr/local/bin/jq", "class": "prebuilt", "by": "bundle-from-url",
         "source": "https://example.org/jq", "source_sha256": H["8"],
         "upstream_source": "https://example.org/jq/source/", "pinned": True}])
    check("SOURCES.md lists it with its source",
          "- `slax/modules/17-jq.sb:usr/local/bin/jq` — <https://example.org/jq> — source: "
          "https://example.org/jq/source/" in sources.markdown(doc), True)
    # upstream_source is optional (#62): the part is listed with no pointer.
    bare = {k: v for k, v in jq.items() if k != "upstream_source"}
    doc = run({"slax/modules/17-jq.sb": H["7"]}, prov([_files_recipe([bare])]))
    check("no upstream_source: listed, the source not named",
          "`slax/modules/17-jq.sb:usr/local/bin/jq` — <https://example.org/jq> — source: not named"
          in sources.markdown(doc), True)
    # A url: entry that pins no sha256 (#62) is marked, as a whole unpinned download is.
    doc = run({"slax/modules/17-jq.sb": H["7"]}, prov([_files_recipe([dict(jq, pinned=False)])]))
    check("an unpinned download is marked in its part's line",
          "source: https://example.org/jq/source/; no sha256 was pinned" in sources.markdown(doc), True)
    # A staged src: entry in the same step is named, and refused by nothing.
    staged = {"root": "project", "path": "recipes/available/x.files", "kind": "dir"}
    doc = run({"slax/modules/17-jq.sb": H["7"]}, prov([_files_recipe([jq], [staged])]))
    check("a staged src: beside it is named",
          (doc["components"][0].get("inputs"), doc["summary"]["unrecorded"]),
          (["recipes/available/x.files"], 0))
    doc = run({"slax/modules/17-jq.sb": H["7"]}, prov([_files_recipe(None)]))
    check("no downloads: no parts, as before", doc["components"][0].get("parts"), None)


def test_a_src_entry_naming_an_upstream_source_is_a_prebuilt_part():
    """#60: a tree the project's build staged -- slax-bottles' Flatpak -- had no way to say
    what it was. A bundle.files `src:` entry that gives upstream_source is listed as a
    prebuilt part of the bundle, pointing there, and named as copied in."""
    step = {"verb": "bundle.files", "output": "slax/modules/30-bottles.sb", "output_sha256": H["7"],
            "copied": [{"path": "var/lib/flatpak", "input": "./bottles.files/var/lib/flatpak",
                        "upstream_source": ["https://github.com/flathub/com.usebottles.bottles"]}]}
    doc = run({"slax/modules/30-bottles.sb": H["7"]}, prov([{"recipe": "bottles", "steps": [step]}]))
    check("a prebuilt part pointing upstream", doc["components"][0].get("parts"), [
        {"member": "var/lib/flatpak", "class": "prebuilt", "by": "bottles",
         "copied_from": "./bottles.files/var/lib/flatpak",
         "upstream_source": ["https://github.com/flathub/com.usebottles.bottles"]}])
    check("and SOURCES.md says so",
          "- `slax/modules/30-bottles.sb:var/lib/flatpak` — copied in from "
          "`./bottles.files/var/lib/flatpak` — source: "
          "https://github.com/flathub/com.usebottles.bottles" in sources.markdown(doc), True)


def test_a_binary_a_script_reported_downloading_is_prebuilt():
    """A file a script reports downloading is a prebuilt part of its bundle (#52, #62).

    Measured at 0dd1b53 for #52 (a): jq's static binary, fetched by bundle.script with a
    KITCHEN-FETCHED line and an upstream_source, was unresolved -- no package owns it and
    no `declares:` names it. #52 then held each line to the file and counted only checked
    lines; #62 removed that check with the refusal it served, so every line is a part, as
    the script reported it, and the note says whose account the list is.
    """
    def recipe(fetched, declared=False):
        r = _script_recipe(declared=declared)
        r["steps"][0].update(fetched=fetched, network=True,
                             upstream_source="https://example.org/tool/source/")
        return r
    line = {"sha256": H["8"], "path": "usr/local/bin/tool", "url": "https://example.org/tool"}
    doc = run({"slax/modules/07-tool.sb": H["7"]}, prov([recipe([line])]))
    comp = (doc["components"] or [{}])[0]
    check("a reported download is a prebuilt part pointing upstream", comp.get("parts"), [
        {"member": "usr/local/bin/tool", "class": "prebuilt", "by": "tool",
         "source": "https://example.org/tool", "source_sha256": H["8"],
         "upstream_source": "https://example.org/tool/source/"}])
    check("the note says whose account it is", "the script's own account" in (comp.get("note") or ""),
          True)
    doc = run({"slax/modules/07-tool.sb": H["7"]}, prov([recipe([dict(line, checked=True)])]))
    check("a line an older kitchen checked reads the same", doc["components"][0].get("parts"),
          comp.get("parts"))
    doc = run({"slax/modules/07-tool.sb": H["7"]}, prov([recipe([line], declared=True)]))
    check("declared and reported: both listed, nothing refused",
          (sorted(p["class"] for p in doc["components"][0]["parts"]), doc["summary"]["unrecorded"]),
          (["built", "prebuilt"], 0))


def main():
    for fn in [test_stock_files_are_slax_by_hash,
               test_a_renumbered_stock_bundle_is_still_slax,
               test_a_changed_stock_file_with_no_record_is_unrecorded,
               test_isolinux_bin_is_recognised_after_the_boot_info_table_is_rewritten,
               test_a_recorded_download_is_prebuilt_only_when_the_bytes_match,
               test_the_cpio_reader_reads_what_cpio_would,
               test_a_broken_archive_is_reported_as_unreadable_not_as_a_traceback,
               test_an_initramfs_that_decompresses_to_more_than_the_cap_is_refused,
               test_an_image_with_no_manifest_is_not_called_unreadable,
               test_a_script_that_had_network_says_so,
               test_the_initramfs_note_counts_members_instead_of_asserting_them,
               test_an_unpinned_download_is_said_out_loud,
               test_a_mirror_path_cannot_impersonate_debian,
               test_packages_point_at_snapshot_including_reinstalls,
               test_a_package_from_a_recipe_repository_points_at_that_repository,
               test_a_package_from_an_undeclared_archive_has_no_pointer,
               test_slackware_packages_point_at_the_release_source_tree,
               test_apt_names_index_files_the_way_apt_does,
               test_a_binary_a_script_left_is_part_of_its_bundle,
               test_busybox_is_built_and_an_unverified_claim_is_said,
               test_grub_is_built_and_its_source_follows_the_builder,
               test_recipe_edits_and_pack_output_are_this_builds,
               test_a_dirty_build_is_a_note_not_a_refusal,
               test_an_image_built_on_another_image_lists_its_files_as_base,
               test_a_sidecar_for_another_iso_is_a_note,
               test_the_mbr_is_a_prebuilt_host_component,
               test_what_a_recipe_copies_in_is_listed_whatever_it_holds,
               test_a_file_from_a_build_host_package_points_at_that_package,
               test_markdown_renders,
               test_a_file_bundle_files_downloaded_is_a_prebuilt_part_of_its_bundle,
               test_a_src_entry_naming_an_upstream_source_is_a_prebuilt_part,
               test_a_binary_a_script_reported_downloading_is_prebuilt]:
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
    print("tests/unit/test_sources.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
