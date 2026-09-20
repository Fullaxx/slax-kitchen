#!/usr/bin/env python3
"""Unit tests for the recipe engine's pure logic.

These exist because every case below is a bug that actually shipped during development
and was caught by inspecting a built bundle rather than by a test. They need no ISO and
take about a second.
"""
import os
import shutil
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "lib"))

import apply  # noqa: E402

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def test_bundle_exclude():
    """A bundle must not carry build scaffolding or package-manager caches.

    Leaking var/lib/slackpkg once put 9 MB of metadata in a bundle whose only real
    payload was one binary. Leaking etc/slackpkg/* would push our own dead-repo pruning
    onto the user's live system. And a pattern ending in a bare "/" still ships the
    empty DIRECTORY entry even when every file inside it is excluded.
    """
    X = apply.BUNDLE_EXCLUDE
    for path in ["var/lib/slackpkg", "var/lib/slackpkg/PACKAGES.TXT",
                 "var/lib/apt", "var/lib/apt/lists/x", "var/cache/apt/x", "var/log/x",
                 "var/lock", "tmp", "tmp/x", "proc", "dev/null", "run/x",
                 "root/.gnupg", "root/.gnupg/pubring.kbx", "root/.wget-hsts",
                 "etc/resolv.conf", "etc/fstab", "etc/mtab", "etc/ld.so.cache",
                 "usr/sbin/policy-rc.d", "etc/apt/apt.conf.d/00kitchen",
                 "etc/slackpkg/mirrors", "etc/slackpkg/slackpkgplus.conf",
                 "var/lib/dpkg/lock", ".wh.something",
                 # A whiteout deletes a lower bundle's file wherever it sits, so the
                 # pattern has to match at any depth -- it used to be root-anchored.
                 "etc/.wh.passwd", "usr/lib/x86_64-linux-gnu/.wh.libnss3.so",
                 "var/.wh..wh.orph",
                 # Debian's package database is ONE FILE, and a union composes trees,
                 # not files. Shipping it replaced 05-chromium's 600 packages with 299.
                 "var/lib/dpkg/status", "var/lib/dpkg/available"]:
        check(f"exclude {path}", bool(X.search(path)), True)
    for path in ["usr/bin/tmux", "usr/bin/ncdu", "etc/tmux.conf", "root/.bashrc",
                 # info/ IS a directory of per-package files, so it unions correctly
                 # and must survive -- it is what makes the package look installed.
                 "var/lib/dpkg/info/tmux.list",
                 "var/lib/slax-kitchen/dpkg-status.d/07-extras",
                 "var/lib/pkgtools/packages/tmux-3.7c-x86_64-2",
                 "etc/slackpkg/blacklist", "usr/doc/tmux-3.7c/CHANGES",
                 "rootfsX/.gnupg",
                 # Not whiteouts: the marker is a path SEGMENT starting ".wh.", so a
                 # file merely containing the string is ordinary content.
                 "usr/share/doc/foo/not.wh.a-whiteout", "etc/wh.conf"]:
        check(f"keep {path}", bool(X.search(path)), False)


def test_bundle_exclude_account_backups():
    """useradd and chpasswd leave the PRE-change files behind.

    /etc/shadow- holds the old hashes, so a recipe whose whole purpose is changing
    /etc/shadow shipped the old one beside the new one. .pwd.lock is a lock, never
    content.
    """
    for bad in ("etc/.pwd.lock", "etc/passwd-", "etc/shadow-", "etc/group-",
                "etc/gshadow-", "etc/subuid-", "etc/subgid-"):
        check(f"{bad} excluded", bool(apply.BUNDLE_EXCLUDE.search(bad)), True)
    # The real files must still ship -- that is the point of the recipe.
    for good in ("etc/passwd", "etc/shadow", "etc/group", "etc/gshadow"):
        check(f"{good} kept", bool(apply.BUNDLE_EXCLUDE.search(good)), False)


def test_slackware_pkgname():
    """Slackware entries are <name>-<version>-<arch>-<build>, and <name> may contain
    hyphens. A prefix match let "gcc" match "gcc-g++-13.2.0-x86_64-1"."""
    for entry, name, want in [("tmux-3.7c-x86_64-2", "tmux", True),
                              ("gcc-g++-13.2.0-x86_64-1", "gcc", False),
                              ("gcc-g++-13.2.0-x86_64-1", "gcc-g++", True),
                              ("gcc-13.2.0-x86_64-1", "gcc", True),
                              ("aaa_base-15.1-x86_64-2", "aaa_base", True),
                              ("ncdu-1.18-x86_64-1_SBo", "ncdu", True)]:
        check(f"pkgname {name} vs {entry}", entry.rsplit("-", 3)[0] == name, want)


def test_when_guard():
    facts = {"flavour": "slackware", "arch": "64bit"}
    for expr, want in [("flavour==slackware", True), ("flavour==debian", False),
                       ("flavour!=debian", True), ("arch==32bit", False),
                       ("arch != 32bit", True)]:
        check(f"when {expr}", apply._when_ok(expr, facts), want)
    for bad in ["bogus==1", "flavour", "flavour~=x"]:
        try:
            apply._when_ok(bad, facts)
            FAILURES.append(f"when {bad!r}: should have raised")
        except RuntimeError:
            pass


def test_subst():
    check("subst", apply.subst({"a": "x{{v}}y", "b": ["{{v}}"]}, {"v": "1"}),
          {"a": "x1y", "b": ["1"]})
    try:
        apply.subst("{{missing}}", {})
        FAILURES.append("subst: undefined var should raise")
    except KeyError:
        pass


def test_extract_member():
    """Archive type must come from CONTENT, not filename.

    A download lands in a temp file named after its destination (memtest.bin.part), so a
    name-based check sends a zip to the tar reader and the whole step fails.
    """
    import tarfile
    import tempfile
    import zipfile
    d = tempfile.mkdtemp()
    z = os.path.join(d, "a.part")
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("dir/hello.txt", "ZIP-OK")
    apply._extract_member(z, "hello.txt", os.path.join(d, "o1"))
    check("extract from zip named .part", open(os.path.join(d, "o1")).read(), "ZIP-OK")

    t = os.path.join(d, "b.part")
    src = os.path.join(d, "h2.txt")
    open(src, "w").write("TAR-OK")
    with tarfile.open(t, "w:gz") as tf:
        tf.add(src, arcname="x/h2.txt")
    apply._extract_member(t, "h2.txt", os.path.join(d, "o2"))
    check("extract from tar.gz named .part", open(os.path.join(d, "o2")).read(), "TAR-OK")

    try:
        apply._extract_member(z, "nope.txt", os.path.join(d, "o3"))
        FAILURES.append("extract: missing member should raise")
    except RuntimeError:
        pass


def test_preflight():
    """Preflight must cover the WHOLE plan before anything runs.

    Without it, applying four recipes with one tool missing downloaded a binary,
    edited two bootloader configs and wrote a pack hint before dying on the fourth.
    """
    # a step whose requirements are all satisfiable
    ok_plan = [("r", {"verb": "boot.menu", "targets": ["isolinux.cfg"]})]
    check("preflight clean plan", apply.preflight(ok_plan, check_network=False), [])

    # requirements are collected per verb, and attributed to the recipe asking
    reqs = apply.step_requires({"verb": "boot.uefi"})
    check("boot.uefi needs grub", "grub-mkstandalone" in reqs["tools"], True)
    check("boot.uefi needs mcopy", "mcopy" in reqs["tools"], True)

    # boot.payload needs the network ONLY when its source is a URL
    check("payload url needs net",
          apply.step_requires({"verb": "boot.payload", "src": "https://x/y.zip"}).get("network"),
          True)
    check("payload local needs no net",
          apply.step_requires({"verb": "boot.payload", "src": "files/y.bin"}).get("network"),
          None)

    # a missing tool is reported with the package that provides it
    saved = apply.shutil.which
    try:
        apply.shutil.which = lambda t: None if t == "mcopy" else "/usr/bin/" + t
        probs = apply.preflight([("uefi-bootable", {"verb": "boot.uefi"})],
                                check_network=False)
        check("missing tool reported", len(probs), 1)
        check("names the recipe", "uefi-bootable" in probs[0], True)
        check("names the package", "mtools" in probs[0], True)
    finally:
        apply.shutil.which = saved


def test_under_containment():
    """A dest: must never resolve outside the tree the verb owns.

    This shipped: `dest: ../../../../x` in a rootcopy.files step wrote x outside the
    work tree entirely, from a recipe declaring `privilege: none`. Recipes are meant to
    be shared, so a verb marked "runs anywhere" has to actually be contained.
    """
    import tempfile
    root = tempfile.mkdtemp()
    inner = os.path.join(root, "tree")
    os.makedirs(os.path.join(inner, "etc"))

    # Ordinary paths resolve, with or without a leading slash.
    check("plain dest", apply._under(inner, "etc/hostname", "v"),
          os.path.join(os.path.realpath(inner), "etc", "hostname"))
    check("absolute dest is treated as tree-relative",
          apply._under(inner, "/etc/hostname", "v"),
          os.path.join(os.path.realpath(inner), "etc", "hostname"))

    for bad in ("../escape", "../../escape", "etc/../../escape", "/../escape"):
        try:
            apply._under(inner, bad, "v")
            FAILURES.append(f"escape not refused: {bad!r}")
        except RuntimeError:
            pass

    # A symlink out of the tree must not become a back door.
    os.symlink("/etc", os.path.join(inner, "host"))
    try:
        apply._under(inner, "host/passwd", "v")
        FAILURES.append("symlink escape not refused")
    except RuntimeError:
        pass

    # A prefix that merely shares characters is not "inside".
    sibling = os.path.join(root, "tree-evil")
    os.makedirs(sibling)
    try:
        apply._under(inner, "../tree-evil/x", "v")
        FAILURES.append("sibling with shared prefix not refused")
    except RuntimeError:
        pass

    apply.shutil.rmtree(root, ignore_errors=True)


def test_wont_do_verbs():
    """Verbs we decided against must explain themselves, not look unfinished.

    They stay in the schema on purpose; the dispatch error is the only place a user
    asking "why can't I do this?" is actually looking.
    """
    for v in ("initramfs.config", "boot.secureboot"):
        check(f"{v} not registered", v in apply.VERBS, False)
        check(f"{v} has a reason", bool(apply.WONT_DO.get(v)), True)
    check("wont-do verbs carry no requirements",
          any(v in apply.VERB_REQUIRES for v in apply.WONT_DO), False)


def test_parse_lsdl():
    """Every xorriso entry type must survive the diff parser.

    The El Torito boot catalog is type 'e', not '-', and report_lba never mentions it.
    A pattern accepting only [-dl] dropped it without a word -- losing the one file
    `uefi-bootable` rewrites from the comparison entirely.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "lib"))
    import diff  # noqa: E402

    # Captured from: xorriso 1.5.6
    cases = [
        ("-rw-r--r--    1 0        0             823 Oct  9  2023 '/readme.txt'",
         "/readme.txt", "file", 823),
        ("drwxr-xr-x    1 0        0               0 Oct  9  2023 '/slax'",
         "/slax", "dir", 0),
        ("er--r--r--    1 0        0            2048 Oct  9  2023 '/slax/boot/isolinux.boot'",
         "/slax/boot/isolinux.boot", "bootcat", 2048),
    ]
    for line, path, kind, size in cases:
        got = diff.parse_lsdl(line)
        check(f"lsdl {kind} parsed", got is not None, True)
        if got:
            check(f"lsdl {kind} path", got[0], path)
            check(f"lsdl {kind} type", got[1]["type"], kind)
            check(f"lsdl {kind} size", got[1]["size"], size)

    check("header line ignored",
          diff.parse_lsdl("Report layout: xt , Startlba ,   Blocks"), None)

    # A bundle 79 MiB in size must not print as bytes.
    check("human MiB", diff._human(82_915_328), "79.1 MiB")
    check("human B", diff._human(823), "823 B")


def test_initramfs_busybox_registered():
    """The busybox verb needs CAP_MKNOD like its siblings.

    The initramfs holds seven device nodes and a non-root cpio silently turns them into
    empty files, so a verb that repacks it must declare the capability or preflight
    cannot warn before the damage is done.
    """
    check("verb registered", "initramfs.busybox" in apply.VERBS, True)
    req = apply.VERB_REQUIRES.get("initramfs.busybox", {})
    check("declares mknod", "mknod" in req.get("caps", []), True)
    for tool in ("cpio", "xz"):
        check(f"declares {tool}", tool in req.get("tools", []), True)
    check("not listed as wont-do", "initramfs.busybox" in apply.WONT_DO, False)


def test_say_does_not_journal():
    """say() tells the user; record() is what provenance means.

    These were one function until the journal became readable, at which point
    "everything we printed" turned out to be ~69 lines of prose per recipe -- warnings,
    indented file lists, and the same bundle recorded twice under two wordings.
    """
    import tempfile
    work = tempfile.mkdtemp()
    ctx = apply.Ctx(work, ".", "t")

    ctx.say("warning: something a user should see but not a thing that changed")
    check("say does not record", ctx.changes, [])

    ctx.record("slax/modules/07-x.sb")
    check("record records", ctx.changes, ["slax/modules/07-x.sb"])

    # Recording the same artifact twice is a double-report, not two artifacts.
    ctx.record("slax/modules/07-x.sb")
    check("record is idempotent", ctx.changes, ["slax/modules/07-x.sb"])

    apply.shutil.rmtree(work, ignore_errors=True)


def test_apt_source_line():
    """A third-party source must not leave the chroot unless the recipe says so.

    Shipping etc/apt/sources.list.d/<name>.list and its key would silently add that
    repository -- and that key's trust -- to the live system of everyone who boots the
    image. `keep: true` is how you say you meant it.
    """
    import tempfile
    from types import SimpleNamespace
    root = tempfile.mkdtemp(prefix="kitchen-apt-test.")
    ctx = SimpleNamespace(say=lambda *_: None)
    src = {"name": "vendor", "uri": "https://example.invalid/deb", "suite": "stable",
           "components": ["main", "contrib"], "architectures": ["amd64"]}

    extra = apply._apt_sources(ctx, root, {"sources": [dict(src)]})
    line = open(os.path.join(root, "etc/apt/sources.list.d/vendor.list")).read()
    check("source line", line.strip(),
          "deb [arch=amd64] https://example.invalid/deb stable main contrib")
    check("list excluded by default",
          apply._excluded("etc/apt/sources.list.d/vendor.list", extra), True)
    check("unrelated path not excluded",
          apply._excluded("usr/bin/vendor-browser", extra), False)

    keep = apply._apt_sources(ctx, root, {"sources": [dict(src, keep=True)]})
    check("keep:true ships the list",
          apply._excluded("etc/apt/sources.list.d/vendor.list", keep), False)


def test_network_declaration():
    """`network: true` was documented in bundle.script's docstring and never read.

    $defs/step had no additionalProperties, so the key validated silently and was
    ignored -- the worst of both worlds: a recipe that looked like it declared
    something, and an engine that did not check it.
    """
    check("bundle.script without network",
          bool(apply.step_requires({"verb": "bundle.script", "script": "x"}).get("network")),
          False)
    check("bundle.script with network: true",
          bool(apply.step_requires({"verb": "bundle.script", "script": "x",
                                    "network": True}).get("network")),
          True)
    # boot.payload still decides from its own argument, which is better than a
    # declaration because it cannot be forgotten.
    check("boot.payload with a URL",
          bool(apply.step_requires({"verb": "boot.payload",
                                    "src": "https://x/y", "dest": "d"}).get("network")),
          True)
    check("boot.payload with a local path",
          bool(apply.step_requires({"verb": "boot.payload",
                                    "src": "files/y", "dest": "d"}).get("network")),
          False)
    check("bundle.packages always needs it",
          bool(apply.step_requires({"verb": "bundle.packages"}).get("network")), True)


def test_profile_recipe_forms():
    """A profile mixes bare names and {name, vars}; both must survive the round trip."""
    import tempfile
    import textwrap
    d = tempfile.mkdtemp(prefix="kitchen-prof-test.")
    path = os.path.join(d, "p.yaml")
    with open(path, "w") as f:
        f.write(textwrap.dedent("""\
            apiVersion: slax-kitchen/v1
            kind: Profile
            metadata:
              name: p
              summary: A throwaway profile exercising both recipe entry forms
            base: {flavour: debian, arch: 64bit, version: "12.2.0"}
            recipes:
              - isohybrid
              - name: serial-console
                vars: {port: ttyS1}
            """))
    names, overrides = apply.read_profile_recipes(path)
    check("order preserved", names, ["isohybrid", "serial-console"])
    check("only the object form contributes overrides",
          overrides, {"serial-console": {"port": "ttyS1"}})


def test_overrides_merge_not_replace():
    """Setting one var must not blank the others.

    `--facts` gets this wrong -- `dict(override) if override else _tree_facts(...)`
    replaces wholesale, so `--facts flavour=debian` discards the derived arch and any
    step guarding on it then fails with "unknown fact 'arch'". Overrides must not
    repeat that.
    """
    declared = {"port": "ttyS0", "speed": "115200"}
    merged = {**declared, **{"port": "ttyS1"}}
    check("overridden key wins", merged["port"], "ttyS1")
    check("untouched key survives", merged["speed"], "115200")


def test_unknown_override_is_rejected():
    """A var the recipe does not declare is an error, not a silent no-op."""
    import tempfile
    import textwrap
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "lib"))
    from validate import validate_file  # noqa: PLC0415
    d = tempfile.mkdtemp(prefix="kitchen-ov-test.")
    path = os.path.join(d, "ovtest.yaml")
    with open(path, "w") as f:
        f.write(textwrap.dedent("""\
            apiVersion: slax-kitchen/v1
            kind: Recipe
            metadata:
              name: ovtest
              summary: A throwaway recipe used to check override rejection
            compat: {flavours: [debian], arch: [64bit], privilege: none}
            vars: {bundle: 07-x}
            steps:
              - {verb: bundle.files, bundle: "{{bundle}}", files: []}
            """))
    check("a declared var is accepted",
          validate_file(path, {"bundle": "20-y"}), [])
    problems = validate_file(path, {"bundel": "20-y"})
    check("a typo is rejected", len(problems), 1)
    # Indexing blind would turn a regression into a traceback instead of a FAIL line,
    # which is a worse test even though it still goes red.
    check("the message names the declared var",
          any("declared: bundle" in p for p in problems), True)


def test_recipe_search_path():
    """Every directory under recipes/ is searched, with available/ first."""
    paths = [os.path.basename(p) for p in apply.recipe_search_path()]
    check("available/ is present and first", paths[0], "available")
    check("the removed examples/ is not there", "examples" in paths, False)


def test_reserved_bundle_numbers():
    """98 and 99 are refused; everything else is the fork's business.

    These two are not style. 98-dpkg-db.sb is deleted and rewritten by `kitchen pack` on
    every pack, so a recipe's bundle there is destroyed silently. And upstream's
    savechanges derives the next session index from the LAST file in slax/modules/ --
    measured with a 99-mystuff.sb present, it recomputes 100 on every boot, so each saved
    session overwrites the one before it.
    """
    for verb in ("bundle.files", "bundle.fromDir", "bundle.fromTarball",
                 "bundle.script", "bundle.packages"):
        for raw in ("98-x", "99-x", "98-dpkg-db", "99-my.tools"):
            try:
                apply._bundle_name(raw, verb)
                FAILURES.append(f"{verb} accepted the reserved name {raw!r}")
            except RuntimeError as e:
                if "reserved" not in str(e):
                    FAILURES.append(f"{verb} refused {raw!r} for the wrong reason: {e}")

        # The escape hatch the message names has to actually work, or the advice is a
        # dead end. 97 sits above every other bundle and collides with nothing.
        check(f"{verb} accepts 97", apply._bundle_name("97-x", verb), "97-x.sb")
        check(f"{verb} accepts 00", apply._bundle_name("00-x", verb), "00-x.sb")
        check(f"{verb} accepts 10", apply._bundle_name("10-x", verb), "10-x.sb")

    # The pre-existing NN- rule must survive unchanged.
    for raw in ("mytools", "5-x"):
        try:
            apply._bundle_name(raw, "bundle.files")
            FAILURES.append(f"accepted a bundle with no NN- prefix: {raw!r}")
        except RuntimeError as e:
            if "NN-" not in str(e):
                FAILURES.append(f"wrong error for {raw!r}: {e}")


def test_renumber_refuses_reserved():
    """bundle.renumber takes a number, not a filename, so it needs its own check."""
    import tempfile
    for to, want_refusal in (("98", True), ("99", True), (99, True),
                             ("97", False), (5, False)):
        work = tempfile.mkdtemp()
        mods = os.path.join(work, "iso", "slax", "modules")
        os.makedirs(mods)
        open(os.path.join(mods, "05-chromium.sb"), "w").close()
        ctx = apply.Ctx(work, ".", "t", dry=True)
        step = {"verb": "bundle.renumber", "match": "^05-chromium", "to": to}
        try:
            apply.v_bundle_renumber(ctx, step)
            if want_refusal:
                FAILURES.append(f"bundle.renumber accepted reserved to={to!r}")
        except RuntimeError as e:
            if not want_refusal:
                FAILURES.append(f"bundle.renumber refused to={to!r}: {e}")
            elif "reserved" not in str(e):
                FAILURES.append(f"bundle.renumber refused to={to!r} wrongly: {e}")


def test_link_targets_refused():
    """A member's NAME being safe says nothing about where its LINK points.

    Shipped: the loop checked m.name and never m.linkname, so an archive holding a
    symlink `x -> /etc` followed by a regular member `x/cron.d/kitchen` wrote through it
    to the host. Measured before the fix, the verb printed "unpacked 2 entries", said
    "built slax/modules/07-poc.sb", exited 0, and left a file outside the work tree.

    The last two cases are the reason the check runs AFTER bundle.fromTarball's `strip`
    rewrite rather than beside the name check: stripping changes a member's depth, so the
    identical link target is safe at one depth and an escape at another.
    """
    import tarfile

    def member(name, linkname, typ):
        m = tarfile.TarInfo(name)
        m.linkname = linkname
        m.type = typ
        return m

    cases = [
        ("absolute symlink target", member("x", "/etc", tarfile.SYMTYPE), True),
        ("relative symlink escape", member("x", "../../etc", tarfile.SYMTYPE), True),
        # data_filter rejects these too; the advisory that prompted this did not mention
        # them, but LNKTYPE resolves against the extraction root just as SYMTYPE does.
        ("absolute hardlink target", member("x", "/etc/passwd", tarfile.LNKTYPE), True),
        ("relative hardlink escape",
         member("x", "../../etc/passwd", tarfile.LNKTYPE), True),
        ("benign sibling symlink", member("x", "y", tarfile.SYMTYPE), False),
        ("benign .. that stays inside", member("a/b/c", "../d", tarfile.SYMTYPE), False),
        ("not a link at all", member("a/b", "", tarfile.REGTYPE), False),
        ("deep enough to absorb ../..", member("a/b/c", "../../etc", tarfile.SYMTYPE),
         False),
        ("same target once strip:1 has shallowed it",
         member("c", "../../etc", tarfile.SYMTYPE), True),
    ]
    for label, m, want_refusal in cases:
        try:
            apply._refuse_escaping_link(m, "bundle.fromTarball")
            refused = False
        except RuntimeError:
            refused = True
        check(f"_refuse_escaping_link: {label}", refused, want_refusal)


def test_fromtarball_refuses_symlink_escape():
    """End to end: a crafted tarball must not write outside the tree being unpacked.

    The verb is `privilege: none` and VERB_REQUIRES asks only for mksquashfs, so a reader
    of a recipe using it has every reason to treat it as harmless. This asserts the
    refusal happens before extraction -- nothing is written and no bundle is built.
    """
    import io
    import tarfile
    import tempfile

    work = tempfile.mkdtemp()
    os.makedirs(os.path.join(work, "iso", "slax", "modules"))
    outside = os.path.join(work, "OUTSIDE")
    os.makedirs(outside)

    archive = os.path.join(work, "evil.tar.gz")
    with tarfile.open(archive, "w:gz") as t:
        link = tarfile.TarInfo("x")
        link.type = tarfile.SYMTYPE
        link.linkname = outside
        t.addfile(link)
        payload = b"planted\n"
        f = tarfile.TarInfo("x/cron.d/kitchen")
        f.size = len(payload)
        t.addfile(f, io.BytesIO(payload))

    ctx = apply.Ctx(work, work, "t")
    step = {"verb": "bundle.fromTarball", "bundle": "07-poc", "src": archive}
    try:
        apply.v_bundle_fromtarball(ctx, step)
        FAILURES.append("bundle.fromTarball extracted an escaping symlink")
    except RuntimeError:
        pass
    check("nothing written outside the tree", os.listdir(outside), [])
    check("no bundle built",
          os.listdir(os.path.join(work, "iso", "slax", "modules")), [])


def test_checksums_sign_is_a_key_id():
    """`sign` is a gpg key id, and every layer disagreed about that.

    The schema typed it boolean, so the form both docs show -- sign: "your-key-id" --
    was a hard validation error, and the ONLY schema-legal truthy value could not sign
    either: str(True) reaches pack.sh as the key id "True". Nothing caught it because no
    shipped recipe sets sign, and 40-schema never validates the YAML fenced in docs/.

    The quote half is tested separately below and is not cosmetic: PyYAML quotes any
    scalar that would reparse as a non-string, so 0xDEADBEEF and an all-digit key id
    arrive with their quotes attached.
    """
    import json
    import tempfile

    import jsonschema

    here = os.path.dirname(os.path.abspath(__file__))
    schema = json.load(open(os.path.join(here, "..", "..", "schema", "recipe.schema.json")))

    def recipe(sign):
        step = {"verb": "iso.checksums", "algorithm": "sha256"}
        if sign is not None:
            step["sign"] = sign
        return {"apiVersion": "slax-kitchen/v1", "kind": "Recipe",
                "metadata": {"name": "signcheck", "summary": "sign field type check"},
                "steps": [step]}

    v = jsonschema.Draft202012Validator(schema)
    for label, value in (("key id", "ABCD1234EF"), ("false", False), ("omitted", None)):
        check(f"schema accepts sign: {label}", list(v.iter_errors(recipe(value))), [])

    # A key id must reach the hints file able to survive pack.sh's sed.
    work = tempfile.mkdtemp()
    ctx = apply.Ctx(work, ".", "t")
    apply.v_iso_checksums(ctx, {"verb": "iso.checksums", "algorithm": "sha256",
                                "sign": "ABCD1234EF"})
    hints = open(os.path.join(work, ".kitchen", "pack.yaml")).read()
    check("hint carries the key id", "checksums_sign: ABCD1234EF" in hints, True)

    # `true` is schema-legal and can never work, so the verb refuses it.
    try:
        apply.v_iso_checksums(ctx, {"verb": "iso.checksums", "sign": True})
        FAILURES.append("iso.checksums accepted sign: true")
    except RuntimeError:
        pass
    # `false` means "no signature", not an error, and must write no hint.
    work2 = tempfile.mkdtemp()
    ctx2 = apply.Ctx(work2, ".", "t")
    apply.v_iso_checksums(ctx2, {"verb": "iso.checksums", "sign": False})
    check("sign: false writes no sign hint",
          "checksums_sign" in open(os.path.join(work2, ".kitchen", "pack.yaml")).read(),
          False)


def test_removes_come_first():
    """A bundle.remove after a bundle.packages deletes ground the bundle stands on.

    `from:` defaults to the whole stack below, so a bundle built that way assumes
    everything beneath it survives to boot. Removing one afterwards leaves an
    unresolvable NEEDED -- and nothing notices, because the file delta is empty for the
    missing libraries, so the fragment does not declare them and the merged database
    stays self-consistent. The build succeeds and passes every gate.

    A profile that removes first and then builds must keep passing: that is what every
    profile here does, with remove-bundle listed ahead of the additive recipes. The
    cross-recipe case is the one that prompted this -- firefox-esr and remove-bundle are
    each fine alone. No recipe mixes the two jobs any more; lib/validate.py refuses one
    that tries, and tests/unit/test_validate.py covers that.
    """
    remove = {"verb": "bundle.remove", "match": "^05-chromium\\.sb$"}
    cases = [
        ("remove then build, two recipes",
         [("remove-bundle", remove),
          ("chromium-current", {"verb": "bundle.packages", "bundle": "10-chromium"})],
         False),
        ("build then remove, one recipe",
         [("r", {"verb": "bundle.packages", "bundle": "20-wine"}), ("r", remove)],
         True),
        ("build then remove, across recipes",
         [("firefox-esr", {"verb": "bundle.packages", "bundle": "11-firefox"}),
          ("remove-bundle", remove)],
         True),
        ("bundle.script counts as building",
         [("r", {"verb": "bundle.script", "bundle": "07-x"}), ("r", remove)],
         True),
        ("removes only", [("remove-bundle", remove)], False),
        ("no bundle verbs at all",
         [("serial-console", {"verb": "boot.append", "args": "console=ttyS0"})], False),
    ]
    for label, plan, want_refusal in cases:
        check(f"check_plan_order: {label}",
              bool(apply.check_plan_order(plan)), want_refusal)

    # THE REMEDY THE MESSAGE NAMES MUST ACTUALLY WORK.
    #
    # The refusal used to end "if the bundle being removed genuinely is not beneath it,
    # say so with an explicit from: on the build step" -- advice that did nothing,
    # because this function never reads from:. A user could follow it exactly and get
    # byte-identical output. Reported against 68879d9 by the slax-wine fork (#5).
    #
    # Asserting on the message text would be brittle. Applying the remedy is not: for
    # every plan that is refused, moving the removes to the front must clear it, and the
    # explicit from: form must still be refused -- that is the promise now being made.
    for label, plan, want_refusal in cases:
        if not want_refusal:
            continue
        removes = [(r, st) for r, st in plan if st.get("verb") == "bundle.remove"]
        rest = [(r, st) for r, st in plan if st.get("verb") != "bundle.remove"]
        check(f"the named remedy clears it: {label}",
              apply.check_plan_order(removes + rest), [])

    # And the remedy that is NOT named must not be implied to work either: an explicit
    # from: changes nothing, by design, which is why the message no longer mentions it.
    for from_ in (["01-core"], ["01-core", "05-chromium"]):
        check(f"explicit from={from_} is still refused",
              bool(apply.check_plan_order(
                  [("r", {"verb": "bundle.packages", "bundle": "20-wine", "from": from_}),
                   ("r", remove)])), True)

    # bundle.renumber disturbs a bundle just as bundle.remove does -- and worse in one
    # way: a remove takes it out of the next _bundle_stack, while a renumber leaves it
    # in place for the build and only then lifts it above. renumber-bundles.yaml ships
    # ^05-chromium -> 95 as its default, so this needs no configuration to hit. (#11)
    check("bundle.renumber counts as disturbing",
          bool(apply.check_plan_order(
              [("r", {"verb": "bundle.packages", "bundle": "11-firefox"}),
               ("r", {"verb": "bundle.renumber", "match": "^05-chromium", "to": "95"})])),
          True)

    # _bundle_stack drops EVERYTHING sorting at or above the bundle being built, not
    # just 98 and 99, so disturbing a higher-numbered bundle afterwards is provably safe
    # and refusing it was pure friction. Conservative when the match is not anchored on
    # a literal number, because a regex can match anything. (#11)
    build10 = {"verb": "bundle.packages", "bundle": "10-foo"}
    for label, step, want_refusal in (
        ("above the target", {"verb": "bundle.remove", "match": "^95-x\\.sb$"}, False),
        ("below the target", {"verb": "bundle.remove", "match": "^05-chromium\\.sb$"}, True),
        ("a saved session", {"verb": "bundle.remove", "match": "^99-changes"}, False),
        ("the generated db", {"verb": "bundle.remove", "match": "^98-dpkg-db\\.sb$"}, False),
        ("unanchored, so unknowable", {"verb": "bundle.remove", "match": "chromium"}, True),
        ("templated, so unknowable", {"verb": "bundle.remove", "match": "{{drop}}"}, True),
    ):
        check(f"over-refusal: {label}",
              bool(apply.check_plan_order([("r", build10), ("r", step)])), want_refusal)

    # Across invocations. The rule held inside one plan and nowhere else, so running the
    # two one-liners every cookbook page documents -- `kitchen apply firefox-esr` then
    # `kitchen apply remove-bundle` -- produced exactly the state the single-plan
    # refusal exists to prevent. The journal is what makes the second run see the first.
    import tempfile
    work = tempfile.mkdtemp()
    mods = os.path.join(work, "iso", "slax", "modules")
    os.makedirs(mods)
    os.makedirs(os.path.join(work, ".kitchen"))
    for b in ("05-chromium.sb", "11-firefox.sb"):
        open(os.path.join(mods, b), "w").close()
    journal = os.path.join(work, ".kitchen", "journal.yaml")
    with open(journal, "w") as f:
        f.write("applied:\n  - recipe: firefox-esr\n    verbs: [bundle.packages]\n"
                "    artifacts: [slax/modules/11-firefox.sb]\n")
    later = [("remove-bundle", {"verb": "bundle.remove", "match": "^05-chromium\\.sb$"})]

    check("a later invocation sees the earlier build",
          bool(apply.check_plan_order(later, work)), True)
    # ...but only while that bundle is still there. Built-then-removed is not ground.
    os.unlink(os.path.join(mods, "11-firefox.sb"))
    check("a bundle since removed is not ground to stand on",
          apply.check_plan_order(later, work), [])
    # ...and a tree with no journal is a fresh unpack: those bundles came with the ISO.
    open(os.path.join(mods, "11-firefox.sb"), "w").close()
    os.unlink(journal)
    check("no journal means nothing was built here",
          apply.check_plan_order(later, work), [])
    # work=None is the --preflight-only path, where kitchen build has not unpacked yet.
    check("work=None falls back to plan-only",
          apply.check_plan_order(later, None), [])

    # And the real shipped recipes must all pass.
    import glob

    import yaml
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(here, "..", "..")
    for f in sorted(glob.glob(os.path.join(root, "recipes", "available", "*.yaml"))):
        doc = yaml.safe_load(open(f))
        name = doc["metadata"]["name"]
        plan = [(name, st) for st in doc.get("steps", []) or []]
        check(f"shipped recipe {name} is accepted",
              apply.check_plan_order(plan), [])


def _fetching_verbs():
    """(tree, funcs, {verb: function}, verbs that urlopen) read from lib/apply.py's AST.

    Shared by the network and provenance tests, so both see the same set of fetchers --
    a verb added with a new urlopen is picked up by both or by neither.
    """
    import ast

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "..", "lib", "apply.py")
    tree = ast.parse(open(path).read())
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]

    def enclosing(line):
        inner = [f for f in funcs if f.lineno <= line <= (f.end_lineno or f.lineno)]
        return min(inner, key=lambda f: (f.end_lineno or f.lineno) - f.lineno) if inner else None

    def verb_of(fn):
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "verb":
                return d.args[0].value
        return None

    def callers_of(name):
        out = []
        for f in funcs:
            for n in ast.walk(f):
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name:
                    out.append(f)
        return out

    by_verb = {verb_of(f): f for f in funcs if verb_of(f)}
    fetchers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not ast.unparse(node.func).endswith("urlopen"):
            continue
        fn = enclosing(node.lineno)
        if fn is None:
            continue
        v = verb_of(fn)
        if v:
            fetchers.add(v)
            continue
        # A helper. Attribute it to every verb that calls it -- _apt_sources is one,
        # reached only from bundle.packages.
        for c in callers_of(fn.name):
            cv = verb_of(c)
            if cv:
                fetchers.add(cv)
    return tree, funcs, by_verb, fetchers


def test_network_is_declared_where_it_is_used():
    """Every verb that reaches the network must say so at preflight.

    bundle.fromTarball did the identical urllib fetch boot.payload does, and the
    inference in step_requires named only boot.payload -- so preflight passed and
    `kitchen build` unpacked 436 MiB before dying at the download. Reported as #9.

    Asserting on a hand-written list of verbs would rot the moment someone adds a
    fetch. This reads lib/apply.py's own AST instead: find every urlopen, resolve it to
    the function containing it, map that to a verb -- directly by its @verb decorator,
    or for a helper via the @verb functions that call it -- and require each one to
    declare network unconditionally or to be in _URL_SRC_VERBS. Finding the answer this
    way is how #9 was confirmed to be exactly one verb and not three.
    """
    _tree, _funcs, _by_verb, fetchers = _fetching_verbs()
    check("found the urlopen verbs at all", bool(fetchers), True)
    for v in sorted(fetchers):
        declares = apply.VERB_REQUIRES.get(v, {}).get("network") is True
        infers = v in apply._URL_SRC_VERBS
        check(f"{v} declares or infers network", declares or infers, True)


def test_fetches_and_bundles_record_provenance():
    """Every verb that downloads something must say what it downloaded, and every bundle
    must record its own hash.

    Provenance that depends on each verb remembering to call ctx.prov rots exactly like
    the network declaration above did (#9): the next verb with a urlopen forgets. So the
    same AST walk finds every fetching verb and requires a ctx.prov call inside it, and
    requires one inside _make_bundle, which every bundle-producing verb goes through.
    Delete either call and this names it.
    """
    import ast

    _tree, funcs, by_verb, fetchers = _fetching_verbs()

    def calls_prov(fn):
        return any(isinstance(n, ast.Call) and ast.unparse(n.func).endswith("ctx.prov")
                   for n in ast.walk(fn))

    check("found the urlopen verbs at all", bool(fetchers), True)
    for v in sorted(fetchers):
        check(f"{v} records provenance", calls_prov(by_verb[v]), True)
    make = next((f for f in funcs if f.name == "_make_bundle"), None)
    check("_make_bundle exists", make is not None, True)
    if make is not None:
        check("_make_bundle records the bundle's hash", calls_prov(make), True)
    # The verb that BUILDS a binary from the host's toolchain -- no urlopen, still the
    # one place the answer to "which GRUB is this" exists.
    check("boot.uefi records the host's GRUB", calls_prov(by_verb["boot.uefi"]), True)


def test_an_elf_a_script_replaced_is_not_vouched_for_by_its_package():
    """_unowned_elf asked only whether SOME package names the path. A script that overwrote
    a packaged binary -- `curl -o /usr/bin/ssh …` -- produced an ELF that no `declares:`
    covered, was not reported, and left `kitchen sources` naming openssh-client as its
    source. dpkg records an md5 for every file it ships, right beside the .list."""
    import shutil
    import tempfile
    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "usr", "bin"))
        info = os.path.join(root, "var", "lib", "dpkg", "info")
        os.makedirs(info)
        elf = os.path.join(root, "usr", "bin", "ssh")
        with open(elf, "wb") as f:
            f.write(b"\x7fELF" + b"original\n")
        with open(os.path.join(info, "openssh-client.list"), "w") as f:
            f.write("/usr/bin/ssh\n")
        import hashlib
        digest = hashlib.md5(open(elf, "rb").read()).hexdigest()
        with open(os.path.join(info, "openssh-client.md5sums"), "w") as f:
            f.write(f"{digest}  usr/bin/ssh\n")

        check("an untouched packaged binary is not reported",
              apply._unowned_elf(root, ["usr/bin/ssh"]), [])

        with open(elf, "wb") as f:                     # the script swaps the bytes
            f.write(b"\x7fELF" + b"replaced\n")
        got = apply._unowned_elf(root, ["usr/bin/ssh"])
        check("the replaced one is", [e["path"] for e in got], ["usr/bin/ssh"])

        # TWO PACKAGES, ONE PATH. A diversion or a Replaces: takeover records the same
        # path twice with different digests; keeping whichever .md5sums os.listdir read
        # last made this answer depend on a directory listing's order. The file matches
        # one of them, so it is vouched for.
        other = hashlib.md5(open(elf, "rb").read()).hexdigest()
        with open(os.path.join(info, "ssh-replacement.md5sums"), "w") as f:
            f.write(f"{other}  usr/bin/ssh\n")
        with open(os.path.join(info, "ssh-replacement.list"), "w") as f:
            f.write("/usr/bin/ssh\n")
        check("a path two packages record",
              apply._unowned_elf(root, ["usr/bin/ssh"]), [])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_boot_payload_copies_a_local_file_and_records_it():
    """RUN the verb, do not read it. The static check above asks only whether the function
    MENTIONS ctx.local; the call added to satisfy it passed two arguments to a method that
    takes one, so every recipe with a recipe-local `src:` died with a TypeError -- after
    the file had been copied and chmodded, leaving the tree half-written.

    A local file is not a download: it is recorded as a local input, and `pinned` (which
    means "the recipe named the sha256 of a file some server served") does not apply."""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp()
    try:
        recipe_dir = os.path.join(tmp, "recipe")
        os.makedirs(recipe_dir)
        payload = os.path.join(recipe_dir, "memtest.bin")
        with open(payload, "wb") as f:
            f.write(b"\x7fELF payload\n")
        ctx = apply.Ctx(os.path.join(tmp, "work"), recipe_dir, "local-payload")
        os.makedirs(ctx.tree)
        ctx.say = lambda *_a, **_k: None
        ctx.begin_step("boot.payload")          # as apply_recipe does around every verb
        apply.v_boot_payload(ctx, {"verb": "boot.payload", "dest": "/slax/boot/memtest.bin",
                                   "src": "memtest.bin", "mode": "0644"})
        ctx.end_step()
        landed = os.path.join(ctx.tree, "slax", "boot", "memtest.bin")
        check("the payload is in the tree", os.path.isfile(landed), True)
        step = ctx.prov_steps[-1]
        check("recorded as a local input", bool(step.get("local_inputs")), True)
        check("and not as an unpinned download", step.get("pinned"), None)
        check("with the file's own name", step.get("source"), "memtest.bin")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_long_pack_hint_survives_the_round_trip():
    """ctx.hint writes <work>/.kitchen/pack.yaml and lib/hints.sh reads it back with sed.
    safe_dump folds a scalar at 80 columns onto a continuation line, and sed takes the
    first line only: a 128-character publisher (the field's own limit) was mastered into
    the image cut off at the last space before column 80, and the structure test then
    compared the ISO against the same truncated string, so nothing noticed."""
    import shutil
    import subprocess
    import tempfile
    work = tempfile.mkdtemp()
    try:
        meta = os.path.join(work, ".kitchen")
        os.makedirs(meta)
        ctx = apply.Ctx.__new__(apply.Ctx)
        ctx.meta, ctx.dry = meta, False
        ctx.say = lambda *_a, **_k: None
        publisher = ("Slax Kitchen build of Slax 12.2.0 with browsers and firmware "
                     "for the lab machines in room 4")
        ctx.hint("publisher", publisher)
        ctx.hint("volid", "SLAX-CUSTOM")
        read = subprocess.run(
            ["sh", "-c", f'. {REPO}/lib/hints.sh; pack_hint "$1" publisher',
             "sh", os.path.join(meta, "pack.yaml")], capture_output=True, text=True)
        check("what pack would master", read.stdout.strip(), publisher)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_a_recipe_listed_twice_is_refused():
    """Naming one recipe twice applied it ONCE, silently: resolve() deduplicates by path,
    and a profile's per-recipe vars are keyed by recipe name, so the second entry's vars
    overwrote the first and then vanished with it.

    That was harmless while every removal had its own preset recipe. With one generic
    remove-bundle, "drop chromium and the firmware bundle" is the obvious two-entry
    mistake, and the answer is one entry with one pattern.
    """
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "twice.yaml")

    def profile(lines):
        with open(path, "w") as f:
            f.write("apiVersion: slax-kitchen/v1\nkind: Profile\nmetadata:\n"
                    "  name: twice\n  summary: A profile written for this test\n"
                    "base: {flavour: debian, arch: 64bit, version: \"12.2.0\"}\n"
                    "recipes:\n" + lines + "output:\n  name: \"x-{{version}}.iso\"\n")
        return path

    try:
        names, _ov = apply.read_profile_recipes(profile("  - remove-bundle\n  - add-packages\n"))
        check("a profile that names each recipe once is fine", names, ["remove-bundle", "add-packages"])

        twice = profile('  - name: remove-bundle\n    vars: {drop: "^05-chromium\\\\.sb$"}\n'
                        '  - name: remove-bundle\n    vars: {drop: "^01-firmware\\\\.sb$"}\n')
        try:
            apply.read_profile_recipes(twice)
            check("listing a recipe twice is refused", "accepted", "refused")
        except RuntimeError as e:
            check("the refusal names the recipe", "remove-bundle" in str(e), True)
            check("and says what to do instead", "one entry" in str(e).lower(), True)
    finally:
        os.unlink(path)
        os.rmdir(d)


def test_recipe_relative_paths_go_through_ctx_local():
    """A file a recipe copies in is `ours` to `kitchen sources` only because Ctx.local
    records where it sat and what it held. A verb that joins recipe_dir itself copies the
    file in unrecorded, and the image then claims an archive covers something nobody
    checked. Downloads and build outputs are the exceptions: they carry upstream_source or
    a build claim, and are not expected to be in a checkout at all.
    """
    import ast
    tree, _funcs, _by_verb, _fetchers = _fetching_verbs()
    # Only the two verbs whose input is never expected in a checkout: a tarball named by
    # URL and sha256, and a build output with its own claim. `boot.payload` is NOT here --
    # it takes either, and when its `src:` is a local file that file is recorded like any
    # other, which is what records() below asks of it.
    allowed = {"__init__", "local", "v_bundle_fromtarball", "v_initramfs_busybox"}

    def records(fn):
        return any(isinstance(n, ast.Call) and ast.unparse(n.func) == "ctx.local"
                   for n in ast.walk(fn))

    offenders = sorted({fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                        for n in ast.walk(fn)
                        if isinstance(n, ast.Attribute) and n.attr == "recipe_dir"
                        and fn.name not in allowed and not records(fn)
                        # a nested function is walked twice; report the innermost owner
                        and not any(isinstance(c, ast.FunctionDef) and c is not fn and n in ast.walk(c)
                                    for c in ast.walk(fn))})
    check("every recipe-relative path is recorded by Ctx.local", offenders, [])
    users = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "ctx.local")
    check("and the verbs do call it", users >= 8, True)


def test_symlink_chain_cannot_escape():
    """A two-member chain escapes a lexical check, so the guard has to be a real one.

    GHSA-p2w2-qh4r-jr53 was published naming 804725c as patched. It was not:
    _refuse_escaping_link resolves with normpath, which collapses `..` textually and
    cannot know an earlier member already put a symlink on disk at a component it is
    collapsing. Each of these three passes that check on its own, and together they
    walked the payload out of the tree entirely -- "files left inside dest: 0".

    The battery runs both halves, because neither is sufficient: _under is blind to
    absolute link targets (it lstrips the leading slash, correctly, for recipe-named
    paths) and the lexical pass is blind to chains. Deleting either reopens an advisory.
    """
    import io
    import shutil
    import tarfile
    import tempfile

    R, S, L, D = tarfile.REGTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE
    cases = [
        ("absolute symlink (the first advisory)", [("x", "/etc", S), ("x/p", "", R)], True),
        ("single .. traversal", [("x", "../..", S), ("x/p", "", R)], True),
        ("two-member chain (the bypass)",
         [("a/d", "..", S), ("e", "a/d/..", S), ("e/O/p", "", R)], True),
        ("three-deep chain",
         [("a/d", "..", S), ("b/e", "../a/d/..", S), ("b/e/O/p", "", R)], True),
        ("hardlink through a planted symlink",
         [("a/d", "..", S), ("h", "a/d/../etc/passwd", L)], True),
        # Legitimate, and real tarballs contain them. `a/d -> ..` points AT the archive
        # root; only realpath tells it from `current -> ..`, which points above it.
        ("relative link to the archive root", [("a/d", "..", S), ("a/f", "", R)], False),
        ("link to the current directory", [("lib", ".", S), ("f", "", R)], False),
        ("plain nested content", [("pkg", None, D), ("pkg/bin", "", R)], False),
        # No payload member, so the realpath pass never sees a write through it. Only the
        # lexical pass refuses this -- and without it the bundle would ship a symlink
        # pointing at an absolute host path.
        ("lone absolute symlink, nothing written through it",
         [("x", "/etc", S)], True),
    ]
    kw = {"filter": "fully_trusted"} if hasattr(tarfile, "fully_trusted_filter") else {}
    for label, spec, want_refusal in cases:
        box = tempfile.mkdtemp()
        dest = os.path.join(box, "dest")
        os.makedirs(dest)
        arc = os.path.join(box, "a.tar.gz")
        with tarfile.open(arc, "w:gz") as t:
            for name, link, typ in spec:
                i = tarfile.TarInfo(name)
                i.type = typ
                if typ in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    i.linkname = link
                    t.addfile(i)
                elif typ == tarfile.DIRTYPE:
                    # 0o755 EXPLICITLY. TarInfo defaults mode to 0o644, which on a DIRECTORY
                    # is no execute bit: the extracted tree cannot be descended, so an
                    # unprivileged user cannot unlink what is inside it and rmtree leaves the
                    # whole fixture behind. This battery is about symlink escape and never
                    # about modes, so the default was never meaningful here -- it just leaked.
                    # Invisible developing as root, which ignores the bits. CI runs
                    # unprivileged and 80-unit's detector named it: "left 1 fixture(s)".
                    i.mode = 0o755
                    t.addfile(i)
                else:
                    body = b"x\n"
                    i.size = len(body)
                    t.addfile(i, io.BytesIO(body))
        try:
            with tarfile.open(arc) as t:
                ms = t.getmembers()
                for m in ms:
                    apply._refuse_escaping_link(m, "bundle.fromTarball")
                apply._extract_members(t, dest, ms, kw)
            refused = False
        except RuntimeError:
            refused = True
        except Exception as e:                       # noqa: BLE001
            # Anything else means the guard did not run and tarfile met the malformed
            # chain itself -- a KeyError from _find_link_target, say. Report it rather
            # than letting it escape: an uncaught exception aborts the run, which reads
            # as "no failures" to anything counting FAIL lines.
            FAILURES.append(f"chain battery: {label} raised "
                            f"{type(e).__name__}, expected RuntimeError")
            refused = True
        check(f"chain battery: {label}", refused, want_refusal)
        stray = [os.path.join(r, f)[len(box) + 1:]
                 for r, _d, fs in os.walk(box) for f in fs
                 if not os.path.join(r, f)[len(box) + 1:].startswith(("dest/", "a.tar.gz"))]
        check(f"chain battery: {label} left nothing outside", stray, [])
        shutil.rmtree(box, ignore_errors=True)


def test_fromtarball_wires_both_guards_in():
    """The verb itself must call both halves -- not just have them available.

    test_symlink_chain_cannot_escape composes the two functions by hand, so it would
    still pass if v_bundle_fromtarball stopped calling one of them. This drives the verb.
    """
    import io
    import tarfile
    import tempfile

    for label, spec in (
        ("absolute symlink", [("x", "/etc", tarfile.SYMTYPE),
                              ("x/p", None, tarfile.REGTYPE)]),
        ("two-member chain", [("a/d", "..", tarfile.SYMTYPE),
                              ("e", "a/d/../..", tarfile.SYMTYPE),
                              ("e/O/p", None, tarfile.REGTYPE)]),
        # No payload member, so the realpath pass never sees a write through it. This
        # one is refused only if the verb still calls the lexical pass -- it is what
        # catches the verb quietly dropping that call.
        ("lone absolute symlink", [("x", "/etc", tarfile.SYMTYPE)]),
    ):
        work = tempfile.mkdtemp()
        os.makedirs(os.path.join(work, "iso", "slax", "modules"))
        arc = os.path.join(work, "evil.tar.gz")
        with tarfile.open(arc, "w:gz") as t:
            for name, link, typ in spec:
                i = tarfile.TarInfo(name)
                i.type = typ
                if typ == tarfile.SYMTYPE:
                    i.linkname = link
                    t.addfile(i)
                else:
                    body = b"planted\n"
                    i.size = len(body)
                    t.addfile(i, io.BytesIO(body))
        ctx = apply.Ctx(work, work, "t")
        step = {"verb": "bundle.fromTarball", "bundle": "07-poc", "src": arc}
        try:
            apply.v_bundle_fromtarball(ctx, step)
            FAILURES.append(f"v_bundle_fromtarball accepted {label}")
        except RuntimeError:
            pass
        check(f"{label}: no bundle built",
              os.listdir(os.path.join(work, "iso", "slax", "modules")), [])


def test_extract_members_matches_extractall_on_a_clean_archive():
    """The containment check must not change what a legitimate tarball produces.

    Checking before each write means extracting member by member, and CPython's
    extractall defers directory attributes on purpose -- "permissions can interfere with
    extraction and extracting contents can reset mtime". A naive loop would give every
    bundle built from a tarball wrong directory modes and mtimes.
    """
    import io
    import stat
    import tarfile
    import tempfile

    box = tempfile.mkdtemp()
    arc = os.path.join(box, "legit.tar.gz")
    with tarfile.open(arc, "w:gz") as t:
        for name, mode in (("pkg", 0o755), ("pkg/etc", 0o700), ("pkg/var", 0o1777)):
            i = tarfile.TarInfo(name)
            i.type = tarfile.DIRTYPE
            i.mode = mode
            i.mtime = 1600000000
            t.addfile(i)
        for name, mode in (("pkg/etc/conf", 0o600), ("pkg/bin", 0o755)):
            body = b"data\n"
            i = tarfile.TarInfo(name)
            i.size = len(body)
            i.mode = mode
            i.mtime = 1600000001
            t.addfile(i, io.BytesIO(body))
        s = tarfile.TarInfo("pkg/link")
        s.type = tarfile.SYMTYPE
        s.linkname = "etc/conf"
        t.addfile(s)

    kw = {"filter": "fully_trusted"} if hasattr(tarfile, "fully_trusted_filter") else {}

    def snap(root):
        out = {}
        for r, ds, fs in os.walk(root):
            for n in ds + fs:
                p = os.path.join(r, n)
                st = os.lstat(p)
                out[p[len(root) + 1:]] = (stat.S_IMODE(st.st_mode), int(st.st_mtime))
        return out

    a = os.path.join(box, "A")
    os.makedirs(a)
    with tarfile.open(arc) as t:
        t.extractall(a, members=t.getmembers(), **kw)
    b = os.path.join(box, "B")
    os.makedirs(b)
    with tarfile.open(arc) as t:
        apply._extract_members(t, b, t.getmembers(), kw)

    check("checked extraction matches extractall exactly", snap(b), snap(a))


def test_stage_delta_preserves_what_the_chroot_had():
    """Staging must ship the ownership and modes dpkg and the recipe's script set.

    Two bugs lived in this loop, in both verbs, because the loop was copy-pasted:

    * directories and symlinks got no chown at all -- shutil.copystat copies mode, times
      and flags and leaves "contents, owner, and group unaffected". users-and-auth built
      /home/slaxuser as root:root 0700, so the account it exists to create could not
      enter its own home directory (#10).

    * setuid and setgid were destroyed on files, in the branch that looked correct. The
      kernel clears them on chown, even root to root, so copy2-then-chown drops the bit.
      chromium-current installs chromium-sandbox, whose whole content is a setuid helper
      (#13). chmod after chown is the order tarfile uses, for this reason.
    """
    import tempfile

    # Changing a file's owner to ANOTHER uid needs root, and the gates job runs as the
    # runner user -- so the ownership half is root-only while the mode half is not. Both
    # are asserted where they can be: the setuid clearing reproduces unprivileged,
    # because the kernel strips the bit on chown even when the ids do not change.
    privileged = hasattr(os, "geteuid") and os.geteuid() == 0
    owner = (1100, 1100) if privileged else (os.getuid(), os.getgid())

    root = tempfile.mkdtemp()
    stage = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "home", "user"))
    os.makedirs(os.path.join(root, "usr", "bin"))
    if privileged:
        os.chown(os.path.join(root, "home", "user"), *owner)
    os.chmod(os.path.join(root, "home", "user"), 0o700)
    open(os.path.join(root, "home", "user", ".bashrc"), "w").close()
    if privileged:
        os.chown(os.path.join(root, "home", "user", ".bashrc"), *owner)
    open(os.path.join(root, "usr", "bin", "helper"), "w").close()
    os.chmod(os.path.join(root, "usr", "bin", "helper"), 0o4755)
    open(os.path.join(root, "usr", "bin", "setgid"), "w").close()
    os.chmod(os.path.join(root, "usr", "bin", "setgid"), 0o2755)
    os.symlink("/etc/passwd", os.path.join(root, "usr", "bin", "link"))
    if privileged:
        # Owned by a NON-root uid, or the assertion below cannot tell a missing lchown
        # from a symlink root happened to create.
        os.lchown(os.path.join(root, "usr", "bin", "link"), *owner)

    keep = ["home/user", "home/user/.bashrc", "usr/bin/helper", "usr/bin/setgid",
            "usr/bin/link"]
    apply._stage_delta(root, keep, stage)

    def own(rel):
        st = os.lstat(os.path.join(stage, rel))
        return (st.st_uid, st.st_gid)

    def mode(rel):
        import stat as st_
        return st_.S_IMODE(os.lstat(os.path.join(stage, rel)).st_mode)

    # Mode, always -- this is the half that reproduces unprivileged, and #13.
    check("directory keeps its mode", mode("home/user"), 0o700)
    check("setuid survives staging", mode("usr/bin/helper"), 0o4755)
    check("setgid survives staging", mode("usr/bin/setgid"), 0o2755)
    if not privileged:
        return
    # Ownership, root only. #10 proper: copystat leaves owner and group alone.
    check("directory keeps its owner", own("home/user"), owner)
    check("file keeps its owner", own("home/user/.bashrc"), owner)
    check("symlink is lchowned, not followed", own("usr/bin/link"), owner)
    # lchown, not chown: following the link would have changed /etc/passwd's owner.
    check("the symlink target was not touched",
          os.lstat("/etc/passwd").st_uid, 0)
    check("symlink target is preserved",
          os.readlink(os.path.join(stage, "usr/bin/link")), "/etc/passwd")


def test_both_chroot_verbs_use_one_staging_loop():
    """The loop was copy-pasted, so any fix to it landed twice or half-landed.

    lib/apply.py already carries a comment about this exact shape -- "which is exactly
    how a rule ends up enforced by four verbs and not the fifth" -- about a different
    rule. This keeps the staging loop from drifting back apart.
    """
    import ast

    here = os.path.dirname(os.path.abspath(__file__))
    tree = ast.parse(open(os.path.join(here, "..", "..", "lib", "apply.py")).read())
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        if fn.name not in ("v_bundle_script", "v_bundle_packages"):
            continue
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "_stage_delta"]
        check(f"{fn.name} stages through the shared helper", len(calls), 1)
        # ...and does not open-code a second copy of it.
        chowns = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                  and ast.unparse(n.func) in ("os.chown", "os.lchown", "shutil.copystat")]
        check(f"{fn.name} has no inline staging left", chowns, [])


def test_fromtarball_refuses_privileged_members():
    """A privilege: none verb must not decide what runs privileged on the image.

    The verb declares privilege: none and asks only for mksquashfs, but extraction runs
    as root whenever apply.py is under sudo for a plan -- and tarfile's fully_trusted
    extraction applies whatever the archive asks for. sha256: is optional here, so an
    unpinned URL's publisher would be choosing. Filed as #4.

    Refused, not stripped: a bundle that silently does less than its archive said is the
    quieter failure, and bundle.script under privilege: chroot is the honest route.

    This also has to hold BEFORE -all-root is enabled for this verb: -all-root rewrites
    ids and leaves modes alone, so it turns a surviving "setuid nobody" into "setuid
    root".
    """
    import tarfile

    def member(name, typ=tarfile.REGTYPE, mode=0o644):
        i = tarfile.TarInfo(name)
        i.type = typ
        i.mode = mode
        return i

    cases = [
        ("plain file", member("usr/share/doc/x"), False),
        ("executable", member("usr/bin/tool", mode=0o755), False),
        ("sticky directory", member("tmp", tarfile.DIRTYPE, 0o1777), False),
        ("symlink", member("lib", tarfile.SYMTYPE, 0o777), False),
        ("setuid binary", member("usr/bin/tool", mode=0o4755), True),
        ("setgid binary", member("usr/bin/tool", mode=0o2755), True),
        ("character device", member("dev/null", tarfile.CHRTYPE, 0o666), True),
        ("block device", member("dev/sda", tarfile.BLKTYPE, 0o660), True),
        ("FIFO", member("run/sock", tarfile.FIFOTYPE, 0o644), True),
    ]
    for label, m, want_refusal in cases:
        try:
            apply._refuse_privileged_member(m, "bundle.fromTarball")
            refused = False
        except RuntimeError:
            refused = True
        check(f"privileged member: {label}", refused, want_refusal)

    # And the verb must call it -- the cases above exercise the helper directly.
    import ast
    here = os.path.dirname(os.path.abspath(__file__))
    tree = ast.parse(open(os.path.join(here, "..", "..", "lib", "apply.py")).read())
    wired = False
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "v_bundle_fromtarball":
            wired = any(isinstance(n, ast.Call)
                        and getattr(n.func, "id", "") == "_refuse_privileged_member"
                        for n in ast.walk(fn))
    check("v_bundle_fromtarball calls the refusal", wired, True)


def test_all_root_is_per_verb():
    """Which bundles get -all-root is a per-verb decision, and it cannot be global.

    The five bundle verbs disagree about what ownership means. bundle.packages and
    bundle.script stage a chroot delta where dpkg and the recipe's script set ownership
    deliberately -- forcing root there would undo _stage_delta and put /home/<user> back
    to root:root, i.e. re-break #10. The other three take content whose ownership is an
    accident of whoever ran kitchen, or of an archive.

    That matters in a way that is easy to miss: built on a developer desktop, uid 1000 on
    Slax is `guest`, so enable-ssh shipped a guest-owned /etc/rc.d/rc.local that root
    executes at every boot (#8).

    -all-root rewrites ids and leaves mode bits alone, so for bundle.fromTarball it is
    only safe because _refuse_privileged_member already rejects setuid and setgid --
    otherwise it would convert `setuid nobody` into `setuid root`.
    """
    import ast

    here = os.path.dirname(os.path.abspath(__file__))
    tree = ast.parse(open(os.path.join(here, "..", "..", "lib", "apply.py")).read())

    want = {
        "bundle.fromDir": True, "bundle.files": True, "bundle.fromTarball": True,
        "bundle.script": False, "bundle.packages": False,
    }
    seen = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_make_bundle"):
            continue
        verb = node.args[3].value
        seen[verb] = any(k.arg == "all_root" and k.value.value is True
                         for k in node.keywords)
    check("every bundle verb accounted for", sorted(seen), sorted(want))
    for verb, expected in want.items():
        check(f"{verb} all_root", seen.get(verb), expected)


def test_bundle_files_refuses_a_setuid_mode():
    """`mode: "4755"` is a line of YAML that reads like a permission and is not.

    bundle.files is privilege: none and now builds with -all-root, so a setuid mode here
    would be a setuid ROOT binary requested by a recipe that looks harmless.
    """
    import tempfile

    work = tempfile.mkdtemp()
    os.makedirs(os.path.join(work, "iso", "slax", "modules"))
    ctx = apply.Ctx(work, work, "t")
    for mode, want_refusal in (("0644", False), ("0755", False),
                               ("4755", True), ("2755", True)):
        step = {"verb": "bundle.files", "bundle": "07-f",
                "files": [{"dest": "/usr/bin/x", "content": "x", "mode": mode}]}
        try:
            apply.v_bundle_files(ctx, step)
            refused = False
        except RuntimeError as e:
            refused = "setuid" in str(e) or "setgid" in str(e)
        check(f"bundle.files mode {mode}", refused, want_refusal)
        sb = os.path.join(work, "iso", "slax", "modules", "07-f.sb")
        if os.path.exists(sb):
            os.unlink(sb)


def test_iso_files_actually_writes_into_the_iso_tree():
    """`iso.files` was implemented, documented, listed as shipped -- and run by nothing.

    Measured 2026-09-16: no recipe used it, no test touched it, so no matrix leg ever
    executed it. Its only existence outside lib/apply.py was a YAML snippet in
    docs/90-reference/verbs.md, and 45-doc-yaml validates that against the SCHEMA --
    which proves the shape and says nothing about the behaviour. The verb could have
    been broken outright with every gate green.

    It was not broken outright. It was broken quietly: see the dry-run case below.
    """
    import tempfile

    work = tempfile.mkdtemp()
    os.makedirs(os.path.join(work, "iso"))
    payload = os.path.join(work, "payload")
    os.makedirs(os.path.join(payload, "sub"))
    open(os.path.join(payload, "one.txt"), "w").write("one")
    open(os.path.join(payload, "sub", "two.txt"), "w").write("two")
    open(os.path.join(work, "single.bin"), "wb").write(b"\x00\x01")

    ctx = apply.Ctx(work, work, "t")
    apply.v_iso_files(ctx, {"verb": "iso.files", "files": [
        # content -> a new file, parent directories created on the way
        {"dest": "/docs/deep/README.txt", "content": "hello"},
        # src -> a single file, resolved relative to the recipe directory
        {"dest": "/firmware/blob.bin", "src": "single.bin"},
        # src -> a DIRECTORY, which copytree's into place
        {"dest": "/extra", "src": "payload"},
        # mode, applied after the write
        {"dest": "/autorun.sh", "content": "#!/bin/sh\n", "mode": "0755"},
    ]})

    iso = os.path.join(work, "iso")
    r = lambda *p: os.path.join(iso, *p)

    # read() rather than open().read(): a verb that wrote NOTHING must show up as a
    # failed check, not as a FileNotFoundError traceback. A traceback aborts main()
    # before the remaining tests run, and this repo has already been bitten once by a
    # harness that counted FAIL lines and read a crash as zero failures.
    def read(*p, binary=False):
        try:
            return open(r(*p), "rb" if binary else "r").read()
        except OSError as e:
            return f"<unreadable: {e.__class__.__name__}>"

    check("iso.files content", read("docs", "deep", "README.txt"), "hello")
    check("iso.files src file", read("firmware", "blob.bin", binary=True), b"\x00\x01")
    check("iso.files src dir", read("extra", "sub", "two.txt"), "two")
    check("iso.files mode",
          oct(os.stat(r("autorun.sh")).st_mode & 0o777) if os.path.exists(r("autorun.sh"))
          else "<no file>", "0o755")

    # Everything it wrote is in the journal, under the recipe's own spelling of the path.
    check("iso.files journal", sorted(ctx.changes),
          ["/autorun.sh", "/docs/deep/README.txt", "/extra", "/firmware/blob.bin"])

    # It writes OUTSIDE /slax/, which is the whole point of the verb -- that is what
    # separates it from iso.metadata and from the bundle verbs.
    check("iso.files stays out of /slax", os.path.exists(r("slax")), False)

    # Containment. dest is recipe-supplied, and a recipe is meant to be shared.
    for bad in ("../../etc/cron.d/x", "/../../etc/passwd"):
        try:
            apply.v_iso_files(ctx, {"verb": "iso.files",
                                    "files": [{"dest": bad, "content": "x"}]})
            refused = False
        except RuntimeError as e:
            refused = "outside the tree" in str(e)
        check(f"iso.files refuses {bad}", refused, True)

    # A dry run must not touch the tree. It DID: os.makedirs ran before the ctx.dry
    # check, so `kitchen build --dry-run` left empty directories behind in the work
    # tree, which a later real build would then master into the ISO. Same ordering bug
    # in boot.payload and rootcopy.files -- all three fixed together, all three asserted
    # here, because one verb's test is what found the other two.
    dry_work = tempfile.mkdtemp()
    os.makedirs(os.path.join(dry_work, "iso"))
    dry = apply.Ctx(dry_work, dry_work, "t", dry=True)
    apply.v_iso_files(dry, {"verb": "iso.files",
                            "files": [{"dest": "/a/b/c.txt", "content": "x"}]})
    apply.v_rootcopy_files(dry, {"verb": "rootcopy.files",
                                 "files": [{"dest": "/etc/d/e.conf", "content": "x"}]})
    try:
        apply.v_boot_payload(dry, {"verb": "boot.payload", "dest": "/slax/boot/f/g.bin",
                                   "src": "https://example.invalid/g.bin",
                                   "sha256": "0" * 64})
    except Exception:
        pass
    di = os.path.join(dry_work, "iso")
    check("iso.files dry writes nothing", os.path.exists(os.path.join(di, "a")), False)
    check("rootcopy.files dry writes nothing",
          os.path.exists(os.path.join(di, "slax", "rootcopy", "etc")), False)
    check("boot.payload dry writes nothing",
          os.path.exists(os.path.join(di, "slax", "boot", "f")), False)
    check("iso.files dry journals nothing", dry.changes, [])

    # The schema requires only `dest`, so {dest: /x} with neither src nor content is a
    # VALID recipe that reaches the verb. Both file-placing verbs answered with a bare
    # KeyError traceback aimed at the engine, not at the recipe author who caused it.
    # rootcopy.preinit already got this right ("need `script` or `src`"); these two did
    # not, because nothing ever ran them with an incomplete spec.
    for verb_fn, label in ((apply.v_iso_files, "iso.files"),
                           (apply.v_rootcopy_files, "rootcopy.files")):
        try:
            verb_fn(ctx, {"verb": label, "files": [{"dest": "/nosource.txt"}]})
            got = "<no error>"
        except KeyError as e:
            got = f"<KeyError {e}>"
        except RuntimeError as e:
            got = "clear" if "needs `src` or `content`" in str(e) else str(e)
        check(f"{label} missing source is a clear error", got, "clear")


def test_relax_modes_widens_without_granting():
    """`world_readable: true` must make a bundle usable by a non-root account, and must
    not turn data into code or hand out anything else on the way.

    The case is real: Tor Browser's tarball is 0700 on every one of its 27 directories
    and 0600 on 185 of its 220 files -- not one group or world bit in the archive. Since
    bundle.fromTarball packs with -all-root, which rewrites ownership and leaves mode
    bits alone, the bundle would be root-only and Slax's `guest` account could not even
    traverse /opt/tor-browser.

    Four properties, each of which was broken on purpose while writing this:
      read is mirrored;
      execute is mirrored ONLY where the owner already had it, so a data file does not
        become runnable;
      setuid/setgid are never added -- only the low 0o055 bits are ever OR-ed in;
      a symlink is skipped, because chmod would follow it to the target.
    """
    import stat as _s
    import tempfile
    tmp = tempfile.mkdtemp(prefix="relax-")
    d = os.path.join(tmp, "sub")
    os.makedirs(d)
    os.chmod(d, 0o700)
    data = os.path.join(d, "data.bin")
    prog = os.path.join(d, "prog")
    already = os.path.join(d, "already")
    with open(data, "w") as f:
        f.write("x")
    with open(prog, "w") as f:
        f.write("x")
    with open(already, "w") as f:
        f.write("x")
    os.chmod(data, 0o600)
    os.chmod(prog, 0o700)
    os.chmod(already, 0o644)
    # The link's target must live OUTSIDE the walked tree, or this proves nothing: a
    # target inside it is widened on its own account, so following the link and not
    # following it end in the same state. Written the weak way first, and `os.stat` for
    # `os.lstat` was then a mutation the test could not see -- 0 failures.
    outside_dir = tempfile.mkdtemp(prefix="relax-outside-")
    outside = os.path.join(outside_dir, "secret")
    with open(outside, "w") as f:
        f.write("x")
    os.chmod(outside, 0o600)
    link = os.path.join(d, "link")
    os.symlink(outside, link)

    touched = apply._relax_modes(tmp)

    mode = lambda p: _s.S_IMODE(os.lstat(p).st_mode)      # noqa: E731
    check("a 0700 directory becomes traversable", oct(mode(d)), oct(0o755))
    check("a 0600 data file becomes readable, not executable",
          oct(mode(data)), oct(0o644))
    check("a 0700 executable stays executable for everyone", oct(mode(prog)), oct(0o755))
    check("an already-open file is left alone", oct(mode(already)), oct(0o644))
    # The symlink itself must not be chmod'ed. On Linux chmod() follows a symlink, so a
    # link pointing out of the tree would widen a file the caller never asked about --
    # which is why the walk uses lstat and skips links.
    check("chmod did not follow the symlink out of the tree",
          oct(mode(outside)), oct(0o600))
    check("every changed path is counted", touched >= 4, True)

    for p2 in (d, data, prog, already):
        if mode(p2) & (_s.S_ISUID | _s.S_ISGID):
            FAILURES.append(f"_relax_modes granted setuid/setgid on {p2}")

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(outside_dir, ignore_errors=True)


def test_apt_reinstall_is_opt_in():
    """`apt-get install` of a package the stock image already has at the same version does
    nothing. firmware-refresh listed firmware-realtek, -atheros, -iwlwifi and -brcm80211 and
    measured: its bundle's dpkg fragment declared five packages, none of those four. The
    flag is what makes a reinstall reach the bundle -- and it must stay opt-in, because on
    by default every recipe would re-unpack whatever it names that is already installed.
    """
    base = ["apt-get", "install", "-y", "-qq", "--no-remove", "--no-install-recommends"]
    check("default: no --reinstall", apply.apt_install_argv({}), base)
    check("reinstall: true adds it", apply.apt_install_argv({"reinstall": True}),
          base + ["--reinstall"])
    check("reinstall: false is the default", apply.apt_install_argv({"reinstall": False}), base)
    check("no_recommends still honoured", apply.apt_install_argv({"no_recommends": False}),
          ["apt-get", "install", "-y", "-qq", "--no-remove"])

    # --no-remove is on EVERY shape and has no opt-out: a recipe cannot turn it off, so
    # the only way it leaves is somebody editing this line, which is the point. Issue #14.
    for shape in ({}, {"reinstall": True}, {"no_recommends": False},
                  {"reinstall": True, "no_recommends": False}):
        check(f"--no-remove present for {shape}",
              "--no-remove" in apply.apt_install_argv(shape), True)


def test_status_removals_is_a_transition_not_a_scan():
    """A package that was installed and is not any more, both ways apt can do it.

    THE FALSE POSITIVE IS THE HARD PART. Stock Debian 12 already ships nine
    `deinstall ok config-files` stanzas, so a check that looked for `deinstall` in the
    result would fire on every build ever run and be switched off inside a day. Only a
    BEFORE -> AFTER transition means anything.

    Both removal shapes have to count, because they fail differently downstream and
    neither can be inferred from the other: `apt-get remove` leaves a `deinstall ok
    config-files` stanza that dpkgdb.delta() puts in the fragment, so the booted image
    reports the package gone; `apt-get purge` deletes the stanza, delta() iterates the
    after side only and says nothing, and the base's entry survives. Issue #14.
    """
    def st(pkg, status="install ok installed", arch="amd64", ver="1.0"):
        return f"Package: {pkg}\nStatus: {status}\nArchitecture: {arch}\nVersion: {ver}\n"

    before = st("foo") + "\n" + st("keep")
    check("apt-get remove: deinstall ok config-files is a removal",
          apply._status_removals(before, st("foo", "deinstall ok config-files") + "\n" + st("keep")),
          ["foo:amd64"])
    check("apt-get purge: a vanished stanza is a removal",
          apply._status_removals(before, st("keep")), ["foo:amd64"])
    check("nothing removed", apply._status_removals(before, before), [])

    # The nine the base already carries: present as deinstall on BOTH sides, so not a
    # transition, so not a removal. This is the assertion that keeps the check usable.
    stale = st("gcc-12-base", "deinstall ok config-files")
    check("a pre-existing deinstall stanza is not a removal",
          apply._status_removals(stale + "\n" + before, stale + "\n" + before), [])

    # An upgrade changes Version and nothing else. dpkgdb.delta() correctly calls that a
    # change; this must not call it a removal.
    check("an ordinary upgrade is not a removal",
          apply._status_removals(st("foo", ver="1.0"), st("foo", ver="2.0")), [])

    # Architecture is part of the key: a package removed for one arch while another stays
    # is a removal of that one, which matters on an image with i386 foreign-arch enabled.
    check("arch is part of the identity",
          apply._status_removals(st("foo", arch="i386") + "\n" + st("foo", arch="amd64"),
                                 st("foo", arch="amd64")),
          ["foo:i386"])


def test_every_file_writing_verb_records_what_it_wrote():
    """A verb that writes into the tree must ctx.record() it.

    boot.menu and boot.branding edited isolinux.cfg and syslinux.cfg and recorded
    nothing, so `kitchen status` never listed the edit -- and `kitchen sources` could
    not attribute it, and reported serial-console's menu entry as an unexplained change
    to a stock file. Found by running `kitchen sources` on the tor profile. Verbs that
    only set pack hints write nothing and are exempt, by name.
    """
    import ast

    _tree, funcs, by_verb, _fetchers = _fetching_verbs()
    names = {f.name: f for f in funcs}

    def reaches(fn, attr, seen=None):
        seen = seen if seen is not None else set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                s = ast.unparse(n.func)
                if s.endswith(attr):
                    return True
                callee = names.get(s)
                if callee is not None and callee.name not in seen:
                    seen.add(callee.name)
                    if reaches(callee, attr, seen):
                        return True
        return False

    hint_only = {"boot.isohybrid", "iso.metadata", "iso.checksums"}
    for v, fn in sorted(by_verb.items()):
        if v in hint_only:
            check(f"{v} writes no files (sets hints only)", reaches(fn, "ctx.record"), False)
            continue
        check(f"{v} records what it writes", reaches(fn, "ctx.record"), True)


def main():
    # EVERY FIXTURE THIS FILE MAKES GOES IN ONE BOX, AND THE BOX GOES AWAY.
    # 17 of this file's 25 mkdtemp() calls had no cleanup, so running this file by hand left
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
    import tempfile
    box = tempfile.mkdtemp(prefix="test_apply-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_bundle_exclude, test_bundle_exclude_account_backups,
                   test_slackware_pkgname, test_when_guard, test_subst,
                   test_extract_member, test_preflight, test_under_containment,
                   test_wont_do_verbs, test_parse_lsdl,
                   test_initramfs_busybox_registered,
                   test_say_does_not_journal, test_apt_source_line,
                   test_network_declaration, test_profile_recipe_forms,
                   test_overrides_merge_not_replace,
                   test_unknown_override_is_rejected,
                   test_recipe_search_path,
                   test_reserved_bundle_numbers,
                   test_renumber_refuses_reserved,
                   test_link_targets_refused,
                   test_fromtarball_refuses_symlink_escape,
                   test_checksums_sign_is_a_key_id,
                   test_removes_come_first,
                   test_network_is_declared_where_it_is_used,
                   test_fetches_and_bundles_record_provenance,
                   test_recipe_relative_paths_go_through_ctx_local,
                   test_boot_payload_copies_a_local_file_and_records_it,
                   test_a_long_pack_hint_survives_the_round_trip,
                   test_a_recipe_listed_twice_is_refused,
                   test_an_elf_a_script_replaced_is_not_vouched_for_by_its_package,
                   test_symlink_chain_cannot_escape,
                   test_fromtarball_wires_both_guards_in,
                   test_extract_members_matches_extractall_on_a_clean_archive,
                   test_stage_delta_preserves_what_the_chroot_had,
                   test_both_chroot_verbs_use_one_staging_loop,
                   test_fromtarball_refuses_privileged_members,
                   test_all_root_is_per_verb,
                   test_bundle_files_refuses_a_setuid_mode,
                   test_iso_files_actually_writes_into_the_iso_tree,
                   test_relax_modes_widens_without_granting,
                   test_apt_reinstall_is_opt_in,
                   test_every_file_writing_verb_records_what_it_wrote,
                   test_status_removals_is_a_transition_not_a_scan]:
            fn()
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_apply.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        # Put the descend bit back before removing. Fixtures here carry deliberately
        # hostile modes -- 0o700 directories, setuid binaries, whole tarballs of them --
        # and any directory without u+x stops an ordinary user removing what is under it.
        # rmtree then fails, ignore_errors swallows it, and the tree stays. top-down, so
        # chmodding a child while visiting its parent is what lets the walk descend.
        for parent, dirs, _files in os.walk(box):
            for d in dirs:
                try:
                    os.chmod(os.path.join(parent, d), 0o700)
                except OSError:
                    pass
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
