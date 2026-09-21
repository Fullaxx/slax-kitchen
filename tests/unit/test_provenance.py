#!/usr/bin/env python3
"""The provenance guard: what counts as a place on the build machine, and where it is checked.

WHY THIS EXISTS. `lib/provenance.py` refuses to record anything that names a place on the
builder, which is right and is the same rule the Tier C ledger enforces. But the rule was
one regex applied everywhere, HOSTISH, and `^/` was one of its alternatives -- so ANY
absolute path was evidence, including one describing the image.

That stopped `profiles/boot-matrix.yaml`, which sets `marker: /var/lib/kitchen-perch-marker`
because `testkit` builds the path by concatenation and a relative value would resolve to
`/unionvar/lib/...`. And boot-matrix is what `ci.yml` builds for the weekly Tier C job, so
the weekly run was broken. Issue #20.

#20 took `^/` out for vars and kept the rest, and the rest was the image's own paths too.
Slax runs as root, so recipes write under /root/ and /home/guest/, and a local input staged
as a tree that mirrors its destination -- `x.files/root/.config/demo`, relative to the
checkout -- was refused. It was refused AFTER the recipe had built its bundle, which stayed
in slax/modules/ unrecorded and shipped. Issue #26. The tests #20 added covered vars, the
field that had failed, and not the class: nothing drove a local input, an artifact or an
output through the guard. So this file tests the image's ordinary paths wherever they land.

HOSTISH is gone. The rule compares against the places this build is actually using, which
cannot mistake the image for the host, and it runs on vars before anything is built.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import provenance as P  # noqa: E402
import traceback

FAILURES = []

# A work tree, as a string. Never created: the rule only compares against it.
WORK = "/srv/somebuilder/work/img"


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


@contextlib.contextmanager
def env(**kv):
    """Set environment variables for one block, then put them back. HOME and PROJECT_ROOT
    decide what counts as this build machine, so a test that depends on them sets them
    rather than inheriting whatever the machine running it has."""
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def hits(record, work=WORK):
    return P.build_machine_hits(record, work=work)


# Every value here is ordinary on Slax, and HOSTISH refused every one of them -- boot-matrix's
# marker only at release, after #20 had exempted it at apply. Each is recorded the way the
# engine records it.
IMAGE = [
    ("#26: a local input staged as a tree mirroring /root",
     {"steps": [{"verb": "bundle.files", "local_inputs": [
         {"root": "project", "path": "recipes/local/root-demo.files/root/.config/demo"}]}]}),
    # docs/90-reference/verbs.md, as written: rootcopy.files records slax/rootcopy<dest>,
    # and iso.files records its dest exactly as the recipe spells it.
    ("the verb reference's rootcopy.files example",
     {"artifacts": ["slax/rootcopy/root/.bashrc"]}),
    ("the verb reference's iso.files example",
     {"artifacts": ["/README.txt", "/LICENSE", "/autorun.sh", "/docs"]}),
    ("rootcopy.files into guest's home",
     {"artifacts": ["slax/rootcopy/home/guest/.config/app"]}),
    ("a bundle.files output under a nested home/",
     {"steps": [{"verb": "bundle.files", "output": "opt/app/home/defaults.cfg"}]}),
    ("a var naming a file in root's home in the image",
     {"vars": {"conf": "/root/.config/app.conf"}}),
    ("testkit's own report default, restated in a profile",
     {"vars": {"report": "/etc/hostname /etc/slax-version /etc/timezone /etc/localtime "
                         "/root/.xinitrc"}}),
    ("boot-matrix's marker, #20's value",
     {"vars": {"marker": "/var/lib/kitchen-perch-marker"}}),
    ("prose that mentions /root/",
     {"redistribution": {"allowed": False,
                         "why": "installs a licensed runtime under /root/.wine"}}),
]


def test_the_image_is_not_the_builder():
    """Nothing in IMAGE names this machine, whoever's home the build runs in."""
    for home in ("/root", "/home/somebuilder"):
        with env(HOME=home, PROJECT_ROOT=None):
            for name, record in IMAGE:
                check(f"HOME={home}: recorded as the image: {name}", hits(record), [])


def test_the_builders_own_places_are_refused():
    """The rule's whole job: a value inside a directory this build is really using, in any
    field -- there are no exempt fields left to forget at a call site."""
    proj = tempfile.mkdtemp(prefix="proj-")
    try:
        with env(HOME="/home/somebuilder", PROJECT_ROOT=proj):
            for name, value in (
                    ("the work tree", WORK + "/iso/slax/modules/40-x.sb"),
                    ("the work tree itself", WORK),
                    ("the kitchen checkout", os.path.join(ROOT, "recipes", "local", "x.tar.gz")),
                    ("the project checkout", os.path.join(proj, "assets", "x.tar.gz")),
                    ("the builder's home", "/home/somebuilder/Downloads/x.tar.gz"),
                    # URLISH let this one through: it was a URL, so it could not be a path.
                    ("a file:// URL into the checkout", "file://" + os.path.join(ROOT, "debs")),
                    ("the checkout inside prose", f"copied from {ROOT}/assets/x"),
            ):
                check(f"refused in vars: {name}", bool(hits({"vars": {"v": value}})), True)
            check("refused outside vars too",
                  bool(hits({"steps": [{"output": os.path.join(ROOT, "x")}]})), True)
            check("a sibling that only shares a prefix is not the work tree",
                  hits({"vars": {"v": "/srv/somebuilder/work/imgs/x"}}), [])

            # DERIVED, NOT CONFIGURED: see build_machine_paths.
            paths = [p.rstrip("/") for p in P.build_machine_paths()]
            check("the kitchen checkout is derived from the code's own location",
                  ROOT in paths, True)
            check("the project checkout comes from project_root()",
                  os.path.realpath(proj) in [os.path.realpath(p) for p in paths], True)
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_a_directory_counts_only_where_a_path_can_begin():
    """A short directory is the tail of many longer paths. The reference container mounts the
    checkout at /work, and the first version of this rule matched it anywhere in a string --
    so the sibling case above failed in CI, and the image's own
    `slax/rootcopy/root/work/notes` was "inside /work". A shape rule again, with the
    checkout's name for the shape. Every other case in this file used a long checkout path,
    which is why nothing here saw it."""
    with env(HOME="/root", PROJECT_ROOT="/work"):
        for value in ("/srv/somebuilder/work/imgs/x", "slax/rootcopy/root/work/notes",
                      "opt/app/work/x", "/workshop/x", "/work.bak/x"):
            check(f"not inside /work: {value}", hits({"vars": {"v": value}}), [])
        for value in ("/work", "/work/assets/x.tar.gz", "file:///work/debs",
                      "copied from /work/assets/x", "PATH=/usr/bin:/work/bin"):
            check(f"inside /work: {value}", bool(hits({"vars": {"v": value}})), True)


def test_the_gaps_are_decisions():
    """What this rule does not catch, and the one thing it refuses wrongly -- asserted, so
    each is a decision rather than a surprise. build_machine_hits' docstring says why."""
    with env(HOME="/root", PROJECT_ROOT=None):
        check("HOME=/root: a builder file under /root, outside the checkout, is recorded",
              hits({"vars": {"v": "/root/Downloads/app.tar.gz"}}), [])
        check("a builder directory that is none of this build's is not caught",
              hits({"vars": {"v": "/opt/someoneelse/artifacts/x"}}), [])
    with env(HOME="/home/guest", PROJECT_ROOT=None):
        check("building as guest refuses the image's own /home/guest/...",
              bool(hits({"vars": {"v": "/home/guest/.config/app"}})), True)
    with env(HOME="/root", PROJECT_ROOT="/work"):
        check("with the checkout at /work, the image's own /work/... is refused",
              bool(hits({"vars": {"v": "/work/data"}})), True)


def test_a_local_input_is_recorded_relative_to_its_checkout():
    """root_relative() is why a local input can never name the builder: it records a path
    inside the kitchen or project checkout, or only a basename. Tested where the promise is
    made rather than policed after it.

    #26's input is the case that matters. Staged as a tree mirroring its destination, it has
    a directory called `root` inside the checkout -- a name, not a place on this machine.
    """
    proj = tempfile.mkdtemp(prefix="proj-")
    outside = tempfile.mkdtemp(prefix="outside-")
    try:
        staged = os.path.join(proj, "recipes", "local", "root-demo.files", "root", ".config",
                              "demo")
        os.makedirs(os.path.dirname(staged))
        with open(staged, "w") as f:
            f.write("demo\n")
        target = os.path.join(outside, "elsewhere")
        with open(target, "w") as f:
            f.write("x\n")
        link = os.path.join(proj, "linked")
        os.symlink(target, link)
        with env(PROJECT_ROOT=proj):
            check("a kitchen file",
                  P.root_relative(os.path.join(ROOT, "lib", "provenance.py")),
                  {"root": "kitchen", "path": "lib/provenance.py"})
            check("#26's input", P.root_relative(staged),
                  {"root": "project", "path": "recipes/local/root-demo.files/root/.config/demo"})
            check("a symlink out of the checkout is judged by where it points",
                  P.root_relative(link), None)
            check("outside both, only a basename is kept",
                  P.local_input(target).get("outside"), "elsewhere")
            check("...and recorded, #26's input names nothing on this machine",
                  hits({"steps": [{"local_inputs": [P.local_input(staged)]}]}), [])
    finally:
        shutil.rmtree(proj, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def fake_work(tmp, entries):
    """The least of a work tree finalize() reads -- .kitchen/provenance.json, written through
    append_recipe the way apply writes it -- and an image to hash."""
    meta = os.path.join(tmp, "work", ".kitchen")
    os.makedirs(meta)
    for e in entries:
        P.append_recipe(meta, e)
    iso = os.path.join(tmp, "out.iso")
    with open(iso, "wb") as f:
        f.write(b"not really an image\n")
    return os.path.join(tmp, "work"), iso


def test_append_records_and_finalize_decides():
    """Nothing is refused mid-apply any more. append_recipe runs after the recipe has built
    its bundle, and a refusal there stranded it -- in slax/modules/, with no journal entry and
    no provenance, and `kitchen pack` shipped it. Issue #26. So append_recipe writes down what
    it is given, and the record is judged whole when it is finalized for publishing.

    Driven through finalize() rather than asserted from its source. The test that stood here
    checked by AST that both call sites passed the same exemption tuple; there is no tuple
    any more, so there is nothing for two sites to disagree about.
    """
    with env(HOME="/root", PROJECT_ROOT=None):
        tmp = tempfile.mkdtemp(prefix="fin-")
        try:
            work, iso = fake_work(tmp, [dict({"recipe": f"r{i}"}, **record)
                                        for i, (_n, record) in enumerate(IMAGE)])
            try:
                dest = P.finalize(work, iso, "genisoimage", None)
            except RuntimeError as e:
                FAILURES.append(f"finalize refused the image's own paths: {e}")
                return
            doc = json.load(io.open(dest, encoding="utf-8"))
            check("every record reaches the sidecar", len(doc["recipes"]), len(IMAGE))
            check("...#26's input intact",
                  doc["recipes"][0]["steps"][0]["local_inputs"][0]["path"],
                  "recipes/local/root-demo.files/root/.config/demo")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        # The work tree, because it is the one directory only finalize's caller knows: a
        # refusal here proves finalize passes it on, which is what the AST test was for.
        # The other directories are the same rule, tested through build_machine_hits above.
        tmp = tempfile.mkdtemp(prefix="fin-")
        try:
            leak = os.path.join(tmp, "work", "iso", "slax", "x")
            try:
                work, iso = fake_work(tmp, [{"recipe": "r", "vars": {"v": leak}}])
            except RuntimeError as e:
                FAILURES.append(f"append_recipe refused mid-apply: {e}")
                return
            raised = False
            try:
                P.finalize(work, iso, "genisoimage", None)
            except RuntimeError:
                raised = True
            check("finalize refuses a var inside the work tree it was given", raised, True)
            check("...and writes no sidecar", os.path.exists(iso + ".provenance.json"), False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


PROFILE = """\
apiVersion: slax-kitchen/v1
kind: Profile
metadata:
  name: p
  summary: one recipe and one var
base:
  flavour: debian
  arch: 64bit
  version: "12.2.0"
recipes:
  - name: testkit
    vars:
      marker: {marker}
"""


def preflight(marker):
    """`kitchen build`'s first pass, with build.sh's arguments: the profile, --preflight-only.

    In-process rather than a subprocess. A fresh interpreter importing apply.py was a
    quarter of a second per call, measured, and this gate runs at every commit and push.
    main() takes argv and returns the exit status, so nothing is lost."""
    import apply
    d = tempfile.mkdtemp(prefix="profile-")
    try:
        path = os.path.join(d, "p.yaml")
        with open(path, "w") as f:
            f.write(PROFILE.format(marker=json.dumps(marker)))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = apply.main(["apply.py", "--profile", path, "--preflight-only",
                             "--facts", "flavour=debian,arch=64bit"])
        return rc, out.getvalue(), err.getvalue()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_var_is_refused_before_anything_is_built():
    """The one input no producer shapes is checked first. kitchen build runs this pass before
    it unpacks the base image, so a refusal leaves nothing behind -- where #26's came after
    the recipe had built its bundle, and left the bundle there.

    Only the refusal is asserted, not the exit status of the in-image case: past this check
    the preflight looks for testkit's tools, which a lint container may not have."""
    rc, out, err = preflight(os.path.join(ROOT, "leak"))
    check("a var inside this checkout exits 2", rc, 2)
    check("...naming the recipe and the var", "testkit.marker" in err, True)
    check("...before the preflight starts", "preflight" in out, False)
    rc, out, err = preflight("/root/.config/kitchen-marker")
    check("an in-image var under /root is not refused",
          "a place on this build machine" in err, False)
    check("...and gets as far as the preflight", out.startswith("preflight "), True)


def main():
    # EVERY FIXTURE THIS FILE MAKES GOES IN ONE BOX, AND THE BOX GOES AWAY -- for the reasons
    # test_release_assets.py gives (#25). tempfile.tempdir steers this process and TMPDIR the
    # apply.py it runs, and both are put back so an importer is not left pointing at nothing.
    box = tempfile.mkdtemp(prefix="test_provenance-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_the_image_is_not_the_builder,
                   test_the_builders_own_places_are_refused,
                   test_a_directory_counts_only_where_a_path_can_begin,
                   test_the_gaps_are_decisions,
                   test_a_local_input_is_recorded_relative_to_its_checkout,
                   test_append_records_and_finalize_decides,
                   test_a_var_is_refused_before_anything_is_built]:
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
        print("tests/unit/test_provenance.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
