#!/usr/bin/env python3
"""Provenance: what a build fetched, built and embedded, recorded as it happens.

    python3 lib/provenance.py finalize --work WORK --iso OUT.iso --backend NAME [--mbr FILE]

WHY THIS EXISTS. The journal (<work>/.kitchen/journal.yaml) records WHAT was applied --
recipes, verbs, artifacts. It never recorded where anything came from: which URL a
payload was fetched from and its sha256, which package versions apt resolved, which
GRUB the build host embedded in the ESP. And `kitchen build` deletes the work tree when
it finishes, so even what was on disk went with it. Publishing an image means saying what
is in it, and "whatever the build machine had that day" is not an answer.

So verbs call ctx.prov(...) while they run, apply_recipe appends each recipe's records to
<work>/.kitchen/provenance.json, and `kitchen pack` finalizes that into
<iso>.provenance.json NEXT TO THE ISO, where it survives the work tree.

NOTHING ABOUT THE HOST, by construction first. Every producer names a file by its basename,
by its path inside the kitchen or project checkout, or by its path inside the image, and
`kitchen sources` looks every local input up in git at the recorded commit. The one input
no producer shapes -- a profile's vars -- is checked against the places this build actually
uses before anything is built, and the finished record is checked the same way at pack and
at release. See build_machine_hits, including for the regex this replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

SCHEMA = "slax-kitchen/provenance/v1"
WORK_FILE = "provenance.json"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def in_image(path: str) -> str:
    """A path inside the image, without its leading slash: `kitchen sources` keys every file
    in an image that way, and looks recorded outputs up against those keys as they are."""
    return (path or "").lstrip("/")


def build_machine_paths(work: str | None = None) -> list[str]:
    """The directories this build is actually using: its work tree, the kitchen checkout, the
    project checkout when there is one, and the home directory of whoever is running it.

    DERIVED, NOT CONFIGURED. An earlier draft read KITCHEN_WORK and KITCHEN_REPO_ROOT from
    the environment, which nothing sets: the rule would have been dead code, and would have
    passed every test written against it. The kitchen checkout is this file's own
    grandparent, the project checkout is project_root(), and the work tree is handed in by
    the caller; all of them are facts at the moment of the check.

    The project checkout was missing until #26. A kitchen vendored into a project --
    vendor/slax-kitchen, the way slax-wine uses it -- is its own checkout, so a var naming a
    file in the project was caught only if $HOME happened to contain it.

    A bare /root, /home or /Users is left out even as $HOME. It says nothing about THIS
    machine that the image does not share: Slax runs as root, so /root is the image's home
    directory too.
    """
    out: list[str] = []
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (work, repo, project_root(), os.environ.get("HOME")):
        if p and p.startswith("/") and p.rstrip("/") not in ("", "/home", "/root", "/Users"):
            p = p.rstrip("/") + "/"
            if p not in out:
                out.append(p)
    return out


def build_machine_hits(obj, work: str | None = None, where: str = "") -> list[str]:
    """Every string in obj that names one of build_machine_paths(), or a path under it.

    Only a place this build is really using counts. A string's shape is never evidence,
    because the image is a Linux system too, and its only user is root.

    WHY NOT A PATTERN. Until #26 this was HOSTISH, a regex of home-directory shapes -- a
    leading slash, /home/, /root/, /Users/, ~/ or a doubled backslash -- run over every
    string in a record. It caught two things in its life, and both were false:

      #20  boot-matrix's `marker: /var/lib/kitchen-perch-marker`, an absolute path in the
           image. That stopped the weekly Tier C job. The fix exempted vars from `^/` and
           kept the rest, and never reached ci/release-verify.py, which still refused it.
      #26  a local input staged as a tree mirroring its destination,
           `recipes/local/x.files/root/.config/demo`. That is checkout-relative by
           construction and could never name the builder. It was refused after the recipe
           had built its bundle, which stayed in slax/modules/ with no journal entry and no
           provenance, and `kitchen pack` shipped it.

    Neither was an accident of those two fields. The image's ordinary paths have exactly
    those shapes: /root/... and /home/guest/... are where a Slax recipe writes, and an
    absolute path is how the verb reference spells a destination. So its own rootcopy.files
    example (`dest: /root/.bashrc`) and iso.files example (`dest: /README.txt`) were both
    refused as written. Each exemption left the same false positive in the next field. And
    it missed what it was for: an apt source `file:///home/...` passed as a URL.

    The work it claimed to do is done elsewhere, by construction. Producers record
    basenames, checkout-relative paths and in-image paths (root_relative, in_image), and
    `kitchen sources` looks every local input up in git at the recorded commit, so a forged
    path is unresolved and a release refuses it.

    WHERE A DIRECTORY COUNTS: only where a path can begin -- at the start of the string, or
    after a character no path component contains (a space, a quote, `=`, `:`, the last `/`
    of file://). Anywhere else it is the tail of some longer path. The first version matched
    it anywhere, and the reference container mounts the checkout at /work (containers/
    README.md, ci.yml), so `/srv/somebuilder/work/imgs/x` and the image's own
    `slax/rootcopy/root/work/notes` were both "inside /work" -- a shape rule again, with the
    checkout's name for the shape. CI caught it; this file had been tested only with long
    checkout paths.

    THE GAPS, stated rather than discovered:
      - $HOME is /root (this container, most Docker builds): a var naming a builder file
        under /root but outside the checkout is recorded as written. It cannot be told apart
        from the image's own /root. Used as a `src:`, the file is recorded as `outside` and
        `kitchen sources` marks it unresolved, so a release still refuses it.
      - A builder directory that is none of these, such as /opt/somebuilder, passes.
      - The one false positive this rule can make: a directory of this build that is also a
        path in the image, named from its start. Build as `guest`, and the image's
        /home/guest/... values are refused; keep the checkout at /work, as the reference
        container does, and an image path under /work is. The message names the directory.
    """
    roots = [(r.rstrip("/"), re.compile(r"(?<![\w.~+-])" + re.escape(r.rstrip("/"))
                                        + r"(?![\w.~+-])"))
             for r in build_machine_paths(work)]
    out: list[str] = []

    def walk(o, at: str) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{at}.{k}" if at else str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{at}[{i}]")
        elif isinstance(o, str):
            for root, pattern in roots:
                if pattern.search(o):
                    out.append(f"{at}: {o!r} (inside {root})")
                    break

    walk(obj, where)
    return out


def _run(argv: list[str]) -> str | None:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def host_package(path: str | None) -> dict | None:
    """The build host's package that owns `path`, with version and source package.

    None when the host has no dpkg (an Arch or Fedora build machine), when nothing owns
    the path, or when the path is absent. None is a finding, not an error: `kitchen
    sources` reports what it cannot attribute rather than pretending.
    """
    if not path or not shutil.which("dpkg-query"):
        return None
    real = os.path.realpath(path)
    for candidate in dict.fromkeys([path, real]):
        out = _run(["dpkg-query", "-S", candidate])
        if not out:
            continue
        # "pkg: /path" or "pkg1, pkg2: /path"; skip diversion notes.
        line = next((ln for ln in out.splitlines() if not ln.startswith("diversion ")), "")
        owner = line.split(":", 1)[0].split(",")[0].strip()
        if not owner:
            continue
        fmt = "${Package}\t${Version}\t${source:Package}\t${source:Version}\t${Architecture}"
        info = _run(["dpkg-query", "-W", f"-f={fmt}", owner])
        if not info:
            return {"package": owner}
        pkg, ver, src, srcver, arch = (info.split("\t") + [""] * 5)[:5]
        return {"package": pkg, "version": ver, "architecture": arch,
                "source": src or pkg, "source_version": srcver or ver}
    return None


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def project_root() -> str | None:
    """The checkout of the project this kitchen builds for: PROJECT_ROOT when set, else the
    superproject when the kitchen is vendored as a git submodule -- vendor/slax-kitchen, the
    way slax-wine uses it -- so a downstream build needs no configuration to be recorded."""
    env = os.environ.get("PROJECT_ROOT")
    if env:
        return os.path.abspath(env)
    out = _run(["git", "-C", REPO, "rev-parse", "--show-superproject-working-tree"])
    return out.strip() or None if out else None


def root_relative(path: str) -> dict | None:
    """Where a local input lives inside the kitchen or project checkout, as a relative path,
    so a later step can find what was built beside it without naming a place on the build
    machine. The kitchen is checked first: vendored into a project, it is the nearer root."""
    real = os.path.realpath(path)
    for name, root in (("kitchen", REPO), ("project", project_root())):
        if root and real.startswith(os.path.realpath(root) + os.sep):
            return {"root": name, "path": os.path.relpath(real, os.path.realpath(root))}
    return None


def content_digest(path: str) -> tuple[str, str, list[str]]:
    """(kind, digest, ELF files) for something a recipe copies into an image.

    A file's digest is its sha256. A directory's is the sha256 of one line per file or
    symlink under it, sorted: `<relative path>\t<f|x|l>\t<sha256 of the content, or of
    the link target>` -- exactly what git_digest() computes from a commit, so the two can
    be compared. Empty directories are left out, because git cannot hold one.
    """
    def is_elf(p: str) -> bool:
        try:
            with open(p, "rb") as f:
                return f.read(4) == b"\x7fELF"
        except OSError:
            return False
    if not os.path.isdir(path):
        return "file", sha256(path), [os.path.basename(path)] if is_elf(path) else []
    lines, elf = [], []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for n in dirnames + filenames:
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, path)
            if os.path.islink(full):
                target = os.readlink(full).encode()
                lines.append(f"{rel}\tl\t{hashlib.sha256(target).hexdigest()}")
            elif n in filenames:
                mode = "x" if os.stat(full).st_mode & 0o111 else "f"
                lines.append(f"{rel}\t{mode}\t{sha256(full)}")
                if is_elf(full):
                    elf.append(rel)
    return "dir", hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest(), sorted(elf)


def git_digest(root: str, commit: str, path: str, kind: str) -> str | None:
    """content_digest() of `path` as commit `commit` of the checkout at `root` holds it, or
    None when the commit does not have it."""
    if kind == "file":
        r = subprocess.run(["git", "-C", root, "cat-file", "blob", f"{commit}:{path}"],
                           capture_output=True)
        return hashlib.sha256(r.stdout).hexdigest() if r.returncode == 0 else None
    r = subprocess.run(["git", "-C", root, "ls-tree", "-r", "-z", commit, "--", path + "/"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    entries = []
    for rec in r.stdout.split(b"\0"):
        if not rec:
            continue
        meta, name = rec.split(b"\t", 1)
        mode, _type, obj = meta.decode().split()
        entries.append((mode, obj, os.path.relpath(name.decode(), path)))
    batch = subprocess.run(["git", "-C", root, "cat-file", "--batch"],
                           input="".join(f"{obj}\n" for _m, obj, _p in entries).encode(),
                           capture_output=True)
    out, pos, lines = batch.stdout, 0, []
    for mode, _obj, rel in entries:
        header_end = out.index(b"\n", pos)
        size = int(out[pos:header_end].split()[2])
        body = out[header_end + 1:header_end + 1 + size]
        pos = header_end + 1 + size + 1
        kind_char = {"120000": "l", "100755": "x"}.get(mode, "f")
        lines.append(f"{rel}\t{kind_char}\t{hashlib.sha256(body).hexdigest()}")
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def local_input(path: str) -> dict:
    """A file or directory a recipe copied into the image: where it sits in the kitchen or
    project checkout (or only its name, when it is in neither), its content digest, and any
    ELF binaries in it -- compiled code, whose source nothing here records."""
    kind, digest, elf = content_digest(path)
    where = root_relative(path)
    if where is None:
        # Outside both checkouts: a file the build host's package manager owns still has a
        # source to point at, the way the isohybrid MBR does.
        where = {"outside": os.path.basename(os.path.realpath(path)),
                 "host_package": host_package(path) if kind == "file" else None}
    return dict(where, kind=kind, digest=digest, elf=elf[:10] or None)


def git_state(root: str | None) -> dict | None:
    """Commit, dirty flag and submodule pins of a checkout, or None outside one."""
    if not root or not os.path.isdir(os.path.join(root, ".git")) and \
            not os.path.isfile(os.path.join(root, ".git")):
        return None
    commit = _run(["git", "-C", root, "rev-parse", "HEAD"])
    if not commit:
        return None
    describe = (_run(["git", "-C", root, "describe", "--always", "--dirty"]) or "").strip()
    subs = {}
    for ln in (_run(["git", "-C", root, "submodule", "status", "--recursive"]) or "").splitlines():
        parts = ln[1:].split()
        if len(parts) >= 2:
            subs[parts[1]] = parts[0]
    return {"commit": commit.strip(), "describe": describe,
            "dirty": describe.endswith("-dirty"), "submodules": subs}


def load_claim(binary: str) -> dict | None:
    """A build claim written beside a binary by the tool that built it, e.g.
    tools/build-busybox.sh writes busybox-1.37.0-i386-static.provenance.json.

    The claim is only as good as its match: `verified` says whether the artifact sha256
    in the claim is the sha256 of the binary actually used.
    """
    path = binary + ".provenance.json"
    if not os.path.isfile(path):
        return None
    try:
        claim = json.load(open(path))
    except (OSError, ValueError):
        return {"file": os.path.basename(path), "verified": False, "error": "unreadable"}
    want = (claim.get("artifact") or {}).get("sha256")
    return {"file": os.path.basename(path), "sha256": sha256(path),
            "verified": bool(want) and want == sha256(binary), "claim": claim}


def _write_atomic(path: str, doc: dict) -> None:
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_work(meta: str) -> dict:
    path = os.path.join(meta, WORK_FILE)
    if os.path.isfile(path):
        try:
            return json.load(open(path))
        except ValueError:
            pass
    return {"schema": SCHEMA, "recipes": []}


def append_recipe(meta: str, entry: dict) -> None:
    """Add one applied recipe's records to <work>/.kitchen/provenance.json."""
    # NO REFUSAL HERE, deliberately. This runs after the recipe has built its bundles, so a
    # refusal here left them in slax/modules/ with no journal entry and no provenance, and
    # `kitchen pack` shipped them (#26). The profile vars are checked by apply.py before
    # anything is built, and the whole record again by finalize().
    doc = load_work(meta)
    doc.setdefault("recipes", []).append(entry)
    _write_atomic(os.path.join(meta, WORK_FILE), doc)


def _tool_version(tool: str) -> str | None:
    flag = {"genisoimage": "--version", "xorriso": "-version"}.get(tool, "--version")
    out = _run([tool, flag])
    return out.splitlines()[0].strip() if out else None


def finalize(work: str, iso: str, backend: str, mbr: str | None) -> str:
    """Write <iso>.provenance.json: the work tree's records plus what pack itself used."""
    import yaml
    meta = os.path.join(work, ".kitchen")
    doc = load_work(meta)
    origin = {}
    opath = os.path.join(meta, "origin.yaml")
    if os.path.isfile(opath):
        origin = yaml.safe_load(open(opath)) or {}
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kitchen = git_state(repo)
    if kitchen is None and os.path.exists(os.path.join(repo, ".git")):
        # Measured: git refuses a checkout another user owns when run as root without
        # sudo (sudo sets SUDO_UID, which git 2.36+ accepts) -- a root container on a
        # bind-mounted checkout. Overriding safe.directory here would switch off the check
        # for everyone; saying so is enough, because `kitchen sources` refuses the result.
        print("provenance: warning: git could not read this kitchen checkout, so the image "
              "records no commit and `kitchen sources` will not accept it. Running as root "
              "in a checkout another user owns? See `git config safe.directory`.",
              file=sys.stderr)
    out = {
        "schema": SCHEMA,
        "kitchen": kitchen,
        "base": {
            "name": os.path.basename(str(origin.get("source_iso", ""))) or None,
            "sha256": origin.get("source_sha256"),
            "size": origin.get("source_size"),
        },
        "recipes": doc.get("recipes", []),
        "pack": {
            "backend": backend,
            "tool": _tool_version(backend),
            "tool_package": host_package(shutil.which(backend)),
            "iso": {"name": os.path.basename(iso), "sha256": sha256(iso),
                    "size": os.path.getsize(iso)},
        },
    }
    # The build host decides which GRUB and which MBR went in, and where their source
    # lives: Debian's archive for one, Launchpad for the other.
    osr = {}
    if os.path.isfile("/etc/os-release"):
        for ln in open("/etc/os-release"):
            k, _, v = ln.strip().partition("=")
            osr[k] = v.strip('"')
    out["builder"] = {"os": osr.get("PRETTY_NAME"), "id": osr.get("ID"),
                      "version_id": osr.get("VERSION_ID")}
    dbgen = os.path.join(work, "iso", "slax", "modules", "98-dpkg-db.sb")
    if os.path.isfile(dbgen):
        out["pack"]["generated"] = [{"path": "slax/modules/98-dpkg-db.sb",
                                     "sha256": sha256(dbgen)}]
    project = project_root()
    if project and os.path.realpath(project) != os.path.realpath(repo):
        out["project"] = git_state(project)
    if mbr:
        out["pack"]["mbr"] = {"file": os.path.basename(mbr), "sha256": sha256(mbr),
                              "package": host_package(mbr)}
    # THE RECORD THAT GETS PUBLISHED, checked whole. This sidecar is what somebody publishing
    # by hand ships, and ci/release-verify.py only sees images that go through
    # ci/release-assets.sh. Producers cannot write these directories and apply.py checked the
    # vars before the build, so what this stops is a regression in either. `work` is this
    # build's work tree, which release-verify cannot know.
    bad = build_machine_hits(out, work=os.path.abspath(work))
    if bad:
        raise RuntimeError("provenance would record a place on this build machine:\n  "
                           + "\n  ".join(bad))
    dest = iso + ".provenance.json"
    _write_atomic(dest, out)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="provenance for a built ISO")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("finalize", help="write <iso>.provenance.json")
    f.add_argument("--work", required=True)
    f.add_argument("--iso", required=True)
    f.add_argument("--backend", required=True)
    f.add_argument("--mbr")
    a = ap.parse_args()
    try:
        dest = finalize(a.work, a.iso, a.backend, a.mbr)
    except RuntimeError as e:
        print(f"provenance: {e}", file=sys.stderr)
        return 1
    print(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
