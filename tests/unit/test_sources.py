#!/usr/bin/env python3
"""Unit tests for `kitchen sources`' classification, which is pure and needs no ISO.

Each case is a shape of image the classifier has to get right, most of them seen for real
while the command was written: a repacked isolinux.bin, a menu edit nobody recorded, a
bundle altered after pack, a script that left a binary behind.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))

import sources  # noqa: E402

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
    check("nothing unresolved", doc["unresolved"], [])


def test_a_changed_stock_file_with_no_record_is_unresolved():
    doc = run({"slax/boot/isolinux.cfg": H["9"]}, prov())
    check("unresolved", len(doc["unresolved"]), 1)
    check("says why", "differs from the stock image" in doc["unresolved"][0]["reason"], True)


def test_isolinux_bin_is_recognised_after_the_boot_info_table_is_rewritten():
    """Every mastering run rewrites bytes 8-63; the rest must still identify it."""
    doc = run({"slax/boot/isolinux.bin": H["9"], "slax/boot/isolinux.bin@64": H["e"]}, prov())
    check("still slax", classes(doc).get("slax/boot/isolinux.bin"), "slax")
    doc = run({"slax/boot/isolinux.bin": H["9"], "slax/boot/isolinux.bin@64": H["8"]}, prov())
    check("a changed tail is not", len(doc["unresolved"]), 1)


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
    check("altered bundle is not classified", classes(doc).get("slax/modules/15-app.sb"), None)
    check("altered bundle is unresolved", [u["path"] for u in doc["unresolved"]],
          ["slax/modules/15-app.sb"])
    check("says it changed", "changed after" in (doc["unresolved"] or [{}])[0].get("reason", ""),
          True)
    # A LATER recipe that edits the file is a legitimate writer: ours, by that recipe.
    later = {"recipe": "edit-later", "steps": [], "artifacts": ["slax/modules/15-app.sb"]}
    doc = run({"slax/modules/15-app.sb": H["4"]}, prov([_tarball_recipe(H["3"]), later]))
    check("later edit is ours", [(c["class"], c["by"]) for c in doc["components"]],
          [("ours", "edit-later")])


def test_a_download_without_upstream_source_warns_or_fails_under_strict():
    p = prov([_tarball_recipe(H["3"], upstream=None)])
    doc = run({"slax/modules/15-app.sb": H["3"]}, p)
    check("warning, not unresolved", (len(doc["warnings"]), len(doc["unresolved"])), (1, 0))
    doc = run({"slax/modules/15-app.sb": H["3"]}, p, strict=True)
    check("unresolved under --strict", len(doc["unresolved"]), 1)


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
    check("no version at all is unresolved, not a crash", len(doc["unresolved"]), 1)


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


def test_a_repository_without_upstream_source_warns_or_fails_under_strict():
    p = prov([_repo_step(WINE_LIST, upstream=None)])
    doc = run({"slax/modules/12-wine.sb": H["5"]}, p)
    check("warning", (len(doc["warnings"]), len(doc["unresolved"])), (1, 0))
    check("unresolved under --strict", len(run({"slax/modules/12-wine.sb": H["5"]}, p,
                                                strict=True)["unresolved"]), 1)


def test_a_package_from_an_undeclared_archive_is_unresolved():
    doc = run({"slax/modules/12-wine.sb": H["5"]},
              prov([_repo_step("ppa.example.org_x_dists_bookworm_main_binary-amd64_Packages")]))
    check("undeclared archive", "does not declare" in (doc["unresolved"] or [{}])[0].get("reason", ""),
          True)
    r = _repo_step(None)
    for d in r["steps"][0]["debs"]:
        d.pop("origin", None)
    doc = run({"slax/modules/12-wine.sb": H["5"]}, prov([r]))
    check("unknown origin with repositories in play", len(doc["unresolved"]), 1)
    doc = run({"slax/modules/12-wine.sb": H["5"]}, prov([_repo_step(None, with_repo=False)]))
    check("no repositories: Debian's archive is the only one", doc["unresolved"], [])


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
    check("no tree named is a warning", len(doc["warnings"]), 1)


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


def test_an_undeclared_binary_left_by_a_script_is_unresolved():
    doc = run({"slax/modules/07-tool.sb": H["7"]}, prov([_script_recipe()]))
    check("unresolved", len(doc["unresolved"]), 1)
    check("names the file", "usr/local/bin/tool" in doc["unresolved"][0]["reason"], True)
    doc = run({"slax/modules/07-tool.sb": H["7"]}, prov([_script_recipe(declared=True)]))
    check("declared resolves", doc["unresolved"], [])
    check("as a built part", doc["components"][0]["parts"][0]["class"], "built")


def test_busybox_needs_a_verified_claim():
    def rec(verified):
        return {"recipe": "initramfs-busybox", "steps": [{
            "verb": "initramfs.busybox", "output": "slax/boot/initrfs.img", "output_sha256": H["1"],
            "claim": {"verified": verified, "claim": {"sources": [{"url": "https://busybox.net/x",
                                                                  "sha256": H["2"]}]}}}]}
    doc = run({"slax/boot/initrfs.img": H["1"]}, prov([rec(False)]))
    check("unverified claim is unresolved", len(doc["unresolved"]), 1)
    doc = run({"slax/boot/initrfs.img": H["1"]}, prov([rec(True)]))
    check("verified claim resolves", doc["unresolved"], [])
    check("busybox is built", doc["components"][0]["parts"][0]["class"], "built")
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
    check("no host record is unresolved", len(doc["unresolved"]), 1)


def test_recipe_edits_and_pack_output_are_ours():
    p = prov([{"recipe": "serial-console", "steps": [], "artifacts": ["slax/boot/isolinux.cfg"]}],
             pack={"iso": {}, "generated": [{"path": "slax/modules/98-dpkg-db.sb", "sha256": H["4"]}]})
    doc = run({"slax/boot/isolinux.cfg": H["9"], "slax/modules/98-dpkg-db.sb": H["4"]}, p)
    check("edit is ours", classes(doc)["slax/boot/isolinux.cfg"], "ours")
    check("generated db is ours", classes(doc)["slax/modules/98-dpkg-db.sb"], "ours")
    check("nothing unresolved", doc["unresolved"], [])


def test_a_dirty_or_unknown_build_is_unresolved():
    doc = run({}, prov(dirty=True))
    check("dirty", len(doc["unresolved"]), 1)
    check("--allow-dirty accepts it", run({}, prov(dirty=True), allow_dirty=True)["unresolved"], [])
    doc = sources.classify({}, STOCK, prov(), None, {})
    check("unknown base", [u["path"] for u in doc["unresolved"]], ["(base image)"])


def test_non_redistributable_recipes_are_named():
    p = prov([{"recipe": "all-browsers", "steps": [],
               "redistribution": {"allowed": False, "why": "proprietary browsers"}}])
    check("named", run({}, p)["not_redistributable"],
          [{"recipe": "all-browsers", "why": "proprietary browsers"}])


def test_the_mbr_is_a_prebuilt_host_component():
    p = prov(pack={"iso": {}, "mbr": {"file": "isohdpfx.bin", "sha256": H["5"], "package": {
        "package": "isolinux", "source": "syslinux", "source_version": "3:6.04-1"}}})
    comp = [c for c in run({}, p)["components"] if c["path"] == "(system area)"][0]
    check("prebuilt", comp["class"], "prebuilt")
    check("epoch encoded", comp["upstream_source"],
          "https://launchpad.net/ubuntu/+source/syslinux/3%3A6.04-1")


FW = "slax/modules/01-firmware.sb"
STOCK["files"][FW] = H["6"]


def test_stock_firmware_without_license_texts_is_said_plainly():
    doc = run({FW: H["6"]}, prov())
    check("stock bundle seen", doc["firmware"]["stock_bundle"], True)
    check("warned", any("removed the license texts" in w for w in doc["warnings"]), True)
    check("not unresolved: it is Slax as published", doc["unresolved"], [])
    check("said in the manifest", "Slax's own build removed" in sources.markdown(doc), True)


def test_firmware_refresh_puts_the_license_texts_back():
    refresh = {"recipe": "firmware-refresh", "steps": [
        {"verb": "bundle.packages", "output": "slax/modules/09-firmware-debian.sb",
         "output_sha256": H["7"], "reinstall": True,
         "debs": [{"package": "firmware-iwlwifi", "version": "20230210-5",
                   "source": "firmware-nonfree", "source_version": "20230210-5"},
                  {"package": "firmware-realtek", "version": "20230210-5",
                   "source": "firmware-nonfree", "source_version": "20230210-5"}]},
        {"verb": "bundle.script", "output": "slax/modules/09-firmware-linux.sb",
         "output_sha256": H["8"], "upstream_source": "https://example.org/linux-firmware",
         "fetched": [{"path": "usr/lib/firmware/amdgpu/x.bin"},
                     {"path": "usr/lib/firmware/LICENSE.amdgpu"},
                     {"path": "usr/lib/firmware/LICENSES/LICENCE.mediatek"},
                     {"path": "usr/lib/firmware/WHENCE"}]}]}
    doc = run({FW: H["6"], "slax/modules/09-firmware-debian.sb": H["7"],
               "slax/modules/09-firmware-linux.sb": H["8"]}, prov([refresh]))
    check("no warning", doc["warnings"], [])
    check("where Debian's texts are", doc["firmware"]["license_texts"],
          [{"recipe": "firmware-refresh", "bundle": "slax/modules/09-firmware-debian.sb",
            "packages": ["firmware-iwlwifi", "firmware-realtek"]}])
    # WHENCE maps firmware to its license; it is not firmware. Counting it as firmware put
    # "54 firmware files" in a real manifest whose recipe fetches 53.
    check("linux-firmware counted", (doc["firmware"]["fetched_firmware"],
                                     doc["firmware"]["fetched_license_files"],
                                     doc["firmware"]["fetched_whence"]), (1, 2, True))


def _recipe_with_inputs(recipe_file, *inputs):
    return {"recipe": "overlay", "recipe_file": recipe_file,
            "steps": [{"verb": "rootcopy.files", "local_inputs": list(inputs)}]}


def test_what_a_recipe_copies_in_must_be_in_the_source_archive():
    inside = {"root": "kitchen", "path": "recipes/available/x.yaml", "kind": "file",
              "digest": H["1"], "in_archive": True}
    doc = run({}, prov([_recipe_with_inputs(inside)]))
    check("committed recipe resolves", doc["unresolved"], [])
    outside = {"outside": "my.yaml", "kind": "file", "digest": H["1"]}
    doc = run({}, prov([_recipe_with_inputs(outside)]), allow_dirty=True)
    check("a recipe outside both checkouts is unresolved, dirty or not",
          [u["path"] for u in doc["unresolved"]], ["(recipe overlay)"])
    changed = dict(inside, path="recipes/available/files", kind="dir", in_archive=False)
    doc = run({}, prov([_recipe_with_inputs(inside, changed)]))
    check("an input the commit does not hold is unresolved", len(doc["unresolved"]), 1)
    doc = run({}, prov([_recipe_with_inputs(inside, changed)]), allow_dirty=True)
    check("...unless the build is allowed to be dirty", doc["unresolved"], [])
    binary = dict(inside, path="recipes/available/tool", elf=["tool"])
    doc = run({}, prov([_recipe_with_inputs(inside, binary)]), allow_dirty=True)
    check("compiled code copied in is unresolved even when committed",
          "compiled code" in (doc["unresolved"] or [{}])[0].get("reason", ""), True)


def test_a_file_from_a_build_host_package_points_at_that_package():
    """locale-timezone-keyboard copies the build host's tzfile into /etc/localtime: a
    recipe cannot reference a path inside the tree it is building. The host's tzdata owns
    it, which is a source pointer like the MBR's."""
    inside = {"root": "kitchen", "path": "recipes/available/l.yaml", "kind": "file",
              "digest": H["1"], "in_archive": True}
    tz = {"outside": "Prague", "kind": "file", "digest": H["2"],
          "host_package": {"package": "tzdata", "source": "tzdata",
                           "source_version": "2024a-3ubuntu1.1"}}
    doc = run({}, prov([_recipe_with_inputs(inside, tz)]))
    check("resolved", doc["unresolved"], [])
    check("pointed at the host package", doc["host_inputs"][0]["published_at"],
          "https://launchpad.net/ubuntu/+source/tzdata/2024a-3ubuntu1.1")
    check("and listed", "the build host's `tzdata`" in sources.markdown(doc), True)
    unowned = dict(tz, host_package=None)
    check("an unowned host file is not", len(run({}, prov([_recipe_with_inputs(inside, unowned)]))
                                             ["unresolved"]), 1)
    tree = dict(tz, kind="dir")
    check("nor a host directory", len(run({}, prov([_recipe_with_inputs(inside, tree)]))
                                      ["unresolved"]), 1)


def test_the_recorded_digest_is_the_one_git_computes():
    """content_digest() at build time and git_digest() at `sources` time must agree, or
    every committed input would look changed. Checked against a real repository."""
    import shutil
    import subprocess
    import tempfile
    import provenance
    if not shutil.which("git"):
        FAILURES.append("git is not installed; the digest cross-check cannot run")
        return
    d = tempfile.mkdtemp()
    try:
        git = ["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git + ["init", "-q"], check=True)
        os.makedirs(f"{d}/files/sub")
        open(f"{d}/files/a.txt", "w").write("hello\n")
        open(f"{d}/files/sub/run.sh", "w").write("#!/bin/sh\n")
        os.chmod(f"{d}/files/sub/run.sh", 0o755)
        os.symlink("../a.txt", f"{d}/files/sub/link")
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "c"], check=True)
        commit = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True,
                                text=True).stdout.strip()
        old = os.environ.get("PROJECT_ROOT")
        os.environ["PROJECT_ROOT"] = d
        try:
            def verdict():
                kind, digest, elf = provenance.content_digest(f"{d}/files")
                li = {"root": "project", "path": "files", "kind": kind, "digest": digest}
                p = {"project": {"commit": commit}, "recipes": [{"recipe": "r", "steps": [
                    {"verb": "bundle.fromDir", "local_inputs": [li]}]}]}
                sources.check_local_inputs(p)
                return li["in_archive"]
            check("committed directory matches", verdict(), True)
            open(f"{d}/files/sub/untracked", "w").write("x")
            check("an untracked file does not", verdict(), False)
            os.remove(f"{d}/files/sub/untracked")
            os.chmod(f"{d}/files/sub/run.sh", 0o644)
            check("nor a lost exec bit", verdict(), False)
        finally:
            if old is None:
                os.environ.pop("PROJECT_ROOT", None)
            else:
                os.environ["PROJECT_ROOT"] = old
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_markdown_renders():
    doc = run({"slax/modules/15-app.sb": H["3"]}, prov([_tarball_recipe(H["3"])]))
    md = sources.markdown(doc)
    check("has the summary table", "| prebuilt | 1 |" in md, True)


def main():
    for fn in [test_stock_files_are_slax_by_hash,
               test_a_changed_stock_file_with_no_record_is_unresolved,
               test_isolinux_bin_is_recognised_after_the_boot_info_table_is_rewritten,
               test_a_recorded_download_is_prebuilt_only_when_the_bytes_match,
               test_a_download_without_upstream_source_warns_or_fails_under_strict,
               test_packages_point_at_snapshot_including_reinstalls,
               test_a_package_from_a_recipe_repository_points_at_that_repository,
               test_a_repository_without_upstream_source_warns_or_fails_under_strict,
               test_a_package_from_an_undeclared_archive_is_unresolved,
               test_slackware_packages_point_at_the_release_source_tree,
               test_apt_names_index_files_the_way_apt_does,
               test_an_undeclared_binary_left_by_a_script_is_unresolved,
               test_busybox_needs_a_verified_claim,
               test_grub_is_built_and_its_source_follows_the_builder,
               test_recipe_edits_and_pack_output_are_ours,
               test_a_dirty_or_unknown_build_is_unresolved,
               test_non_redistributable_recipes_are_named,
               test_the_mbr_is_a_prebuilt_host_component,
               test_stock_firmware_without_license_texts_is_said_plainly,
               test_firmware_refresh_puts_the_license_texts_back,
               test_what_a_recipe_copies_in_must_be_in_the_source_archive,
               test_a_file_from_a_build_host_package_points_at_that_package,
               test_the_recorded_digest_is_the_one_git_computes,
               test_markdown_renders]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_sources.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
