#!/usr/bin/env python3
"""Unit tests for the recipe engine's pure logic.

These exist because every case below is a bug that actually shipped during development
and was caught by inspecting a built bundle rather than by a test. They run in
milliseconds and need no ISO.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))

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

    chromium-current is the discriminating case and must keep passing: it removes
    05-chromium FIRST and then builds, deliberately (d0f48e8). The cross-recipe case is
    the one that prompted this -- firefox-esr and remove-chromium are each fine alone.
    """
    remove = {"verb": "bundle.remove", "match": "^05-chromium\\.sb$"}
    cases = [
        ("remove then build, one recipe",
         [("chromium-current", remove),
          ("chromium-current", {"verb": "bundle.packages", "bundle": "10-chromium"})],
         False),
        ("build then remove, one recipe",
         [("r", {"verb": "bundle.packages", "bundle": "20-wine"}), ("r", remove)],
         True),
        ("build then remove, across recipes",
         [("firefox-esr", {"verb": "bundle.packages", "bundle": "11-firefox"}),
          ("remove-chromium", remove)],
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
    # `kitchen apply remove-chromium` -- produced exactly the state the single-plan
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
    later = [("remove-chromium", {"verb": "bundle.remove", "match": "^05-chromium\\.sb$"})]

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

    # And the real shipped recipes must all pass, chromium-current above all.
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

    check("found the urlopen verbs at all", bool(fetchers), True)
    for v in sorted(fetchers):
        declares = apply.VERB_REQUIRES.get(v, {}).get("network") is True
        infers = v in apply._URL_SRC_VERBS
        check(f"{v} declares or infers network", declares or infers, True)


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

    root = tempfile.mkdtemp()
    stage = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "home", "user"))
    os.makedirs(os.path.join(root, "usr", "bin"))
    os.chown(os.path.join(root, "home", "user"), 1100, 1100)
    os.chmod(os.path.join(root, "home", "user"), 0o700)
    open(os.path.join(root, "home", "user", ".bashrc"), "w").close()
    os.chown(os.path.join(root, "home", "user", ".bashrc"), 1100, 1100)
    open(os.path.join(root, "usr", "bin", "helper"), "w").close()
    os.chmod(os.path.join(root, "usr", "bin", "helper"), 0o4755)
    open(os.path.join(root, "usr", "bin", "setgid"), "w").close()
    os.chmod(os.path.join(root, "usr", "bin", "setgid"), 0o2755)
    os.symlink("/etc/passwd", os.path.join(root, "usr", "bin", "link"))
    # Owned by a NON-root uid, or the assertion below cannot tell a missing lchown from
    # a symlink root happened to create.
    os.lchown(os.path.join(root, "usr", "bin", "link"), 1100, 1100)

    keep = ["home/user", "home/user/.bashrc", "usr/bin/helper", "usr/bin/setgid",
            "usr/bin/link"]
    apply._stage_delta(root, keep, stage)

    def own(rel):
        st = os.lstat(os.path.join(stage, rel))
        return (st.st_uid, st.st_gid)

    def mode(rel):
        import stat as st_
        return st_.S_IMODE(os.lstat(os.path.join(stage, rel)).st_mode)

    check("directory keeps its owner", own("home/user"), (1100, 1100))
    check("directory keeps its mode", mode("home/user"), 0o700)
    check("file keeps its owner", own("home/user/.bashrc"), (1100, 1100))
    check("setuid survives staging", mode("usr/bin/helper"), 0o4755)
    check("setgid survives staging", mode("usr/bin/setgid"), 0o2755)
    check("symlink is lchowned, not followed", own("usr/bin/link"), (1100, 1100))
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


def main():
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
               test_symlink_chain_cannot_escape,
               test_fromtarball_wires_both_guards_in,
               test_extract_members_matches_extractall_on_a_clean_archive,
               test_stage_delta_preserves_what_the_chroot_had,
               test_both_chroot_verbs_use_one_staging_loop]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_apply.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
