#!/usr/bin/env python3
"""The provenance guard: what counts as a path on the build machine, and where.

WHY THIS EXISTS. `lib/provenance.py` refuses to record anything that names a place on the
builder, which is right and is the same rule the Tier C ledger enforces. But the rule was
one regex applied everywhere, and `^/` was one of its alternatives -- so ANY absolute path
was evidence, including one describing the image.

That stopped `profiles/boot-matrix.yaml`, which sets `marker: /var/lib/kitchen-perch-marker`
because `testkit` builds the path by concatenation and a relative value would resolve to
`/unionvar/lib/...`. And boot-matrix is what `ci.yml` builds for the weekly Tier C job, so
the weekly run was broken. Issue #20.

Nothing caught it because nothing tested this file: `grep -rl hostish_values tests/`
returned nothing before this. A guard with no test is how a rule that stops your own
profiles ships green.
"""
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import provenance as P  # noqa: E402

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def flagged(value, work=None):
    """Is `value` refused when it sits in a recipe's vars?"""
    return bool(P.hostish_values({"recipe": "r", "vars": {"m": value}},
                                 image_paths_ok=("vars",), work=work))


def test_an_in_image_absolute_var_is_not_a_host_path():
    """The exact value that broke the weekly job, and the shapes that still must not pass."""
    check("boot-matrix's marker is accepted",
          flagged("/var/lib/kitchen-perch-marker"), False)
    check("an absolute in-image binary path is accepted",
          flagged("/usr/local/bin/tor-browser"), False)
    check("a relative path is accepted", flagged("var/lib/marker"), False)
    check("a URL is accepted", flagged("https://example.invalid/x"), False)

    # Every shape signal survives. These are the leaks the guard exists for, and none of
    # them stopped being evidence just because `^/` did.
    for leak in ("/home/someone/build", "/root/code/thing", "/Users/someone/build",
                 "~/build/out", "C:\\\\Users\\\\someone"):
        check(f"still refused in vars: {leak}", flagged(leak), True)


def test_a_path_this_build_used_is_refused_even_without_a_shape():
    """The half that is not a guess.

    A builder working somewhere the shape rules know nothing about -- /opt, /srv, /build --
    is only catchable by comparing against where this build actually is. That is why
    build_machine_paths derives the repo root from this file's own location and takes the
    work tree from append_recipe, rather than reading environment variables: an earlier
    draft read KITCHEN_WORK and KITCHEN_REPO_ROOT, which nothing in the tree sets, so this
    half would have been dead code that passed every test written against it.
    """
    work = "/opt/somebuilder/work/img"
    check("a value under this build's work tree is refused",
          flagged(work + "/iso/slax", work=work), True)
    check("...while a sibling path that merely looks similar is not",
          flagged("/opt/somebuilder/workshop/notes", work=work), False)

    repo = ROOT
    check("a value under the repo root is refused", flagged(repo + "/lib/apply.py"), True)
    check("the repo root is derived, not configured",
          any(p.rstrip("/") == repo for p in P.build_machine_paths()), True)

    # The accepted gap, asserted so it is a decision rather than a surprise: an unusual
    # builder path that is neither a known shape nor this build's own is not caught.
    check("the documented gap: an unrelated absolute path passes",
          flagged("/opt/someoneelse/artifacts"), False)


def test_hostish_is_unchanged_outside_vars():
    """release-verify and the ledger gate import this. They must keep their teeth.

    `ci/checks/97-tier-c-ledger.sh` uses HOSTISH directly to refuse an absolute `iso_name`,
    and `ci/release-verify.py` runs hostish_values over a whole published record. Relaxing
    the rule for vars must not relax it for either.
    """
    check("an absolute path outside vars is still a leak",
          bool(P.hostish_values({"iso_name": "/abs/path.iso"})), True)
    check("...and still is when vars are exempted elsewhere in the same document",
          bool(P.hostish_values({"iso_name": "/abs/path.iso", "vars": {"m": "/var/lib/x"}},
                                image_paths_ok=("vars",))), True)
    check("HOSTISH still matches a bare absolute path",
          bool(P.HOSTISH.search("/anything")), True)


def test_a_refused_recipe_leaves_no_provenance():
    """append_recipe writes nothing when it refuses.

    Paired with the ordering in apply_recipe: provenance is written BEFORE the journal, so a
    refusal leaves neither. Written the other way round, a refused apply left a journal
    entry saying the recipe had been applied -- and check_plan_order's _built_before reads
    that journal, so the next run believed it had already happened.
    """
    tmp = tempfile.mkdtemp(prefix="prov-")
    try:
        meta = os.path.join(tmp, "work", ".kitchen")
        os.makedirs(meta)
        raised = False
        try:
            P.append_recipe(meta, {"recipe": "r", "vars": {"m": "/root/code/leak"}})
        except RuntimeError:
            raised = True
        check("a leaking var is refused", raised, True)
        check("...and nothing was recorded",
              os.path.exists(os.path.join(meta, P.WORK_FILE)), False)

        # Caught rather than propagated: an uncaught raise here aborts the whole suite and
        # hides every later check. Collect it like any other failure.
        try:
            P.append_recipe(meta,
                            {"recipe": "r", "vars": {"m": "/var/lib/kitchen-perch-marker"}})
        except RuntimeError as e:                      # noqa: BLE001
            FAILURES.append(f"an in-image var was refused: {e}")
            return
        doc = json.load(io.open(os.path.join(meta, P.WORK_FILE), encoding="utf-8"))
        check("an in-image var is recorded", len(doc["recipes"]), 1)
        check("...with the leading slash intact, because that is what the image uses",
              doc["recipes"][0]["vars"]["m"], "/var/lib/kitchen-perch-marker")

        # THROUGH append_recipe, not hostish_values directly: the exemption list lives at
        # the call site, so a test that only drives the helper cannot see it widen. Adding
        # "iso_name" to that tuple was a mutation this file did not catch until now.
        raised = False
        try:
            P.append_recipe(meta, {"recipe": "r", "iso_name": "/abs/path.iso", "vars": {}})
        except RuntimeError:
            raised = True
        check("append_recipe still refuses an absolute path outside vars", raised, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_both_validation_sites_exempt_vars():
    """Every place that validates a record must exempt `vars`, or the two drift.

    THIS IS THE TEST THAT WAS MISSING. hostish_values is called twice in provenance.py to
    refuse a record: append_recipe, at apply time, and finalize, at pack time. Fixing only
    the first left `kitchen build boot-matrix` failing at the very last step -- the ISO
    written, and then "could not write ...provenance.json" -- while every unit test here
    passed, because none of them went through finalize.

    Checked structurally rather than by driving finalize, which needs a whole work tree: the
    property is "both call sites agree", and that is visible in the source. Same shape as
    test_all_root_is_per_verb and test_both_chroot_verbs_use_one_staging_loop, which exist
    for the same reason -- two places that must not diverge.
    """
    import ast
    src = io.open(os.path.join(ROOT, "lib", "provenance.py"), encoding="utf-8").read()
    tree = ast.parse(src)

    # A call is "validating" when its result feeds a refusal: `bad = hostish_values(...)`.
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") == "hostish_values"):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "bad" in names:
            sites.append((node.lineno, {k.arg for k in node.value.keywords}))

    check("both validation sites found", len(sites), 2)
    for lineno, kwargs in sites:
        check(f"provenance.py:{lineno} exempts vars", "image_paths_ok" in kwargs, True)
        check(f"provenance.py:{lineno} passes the work tree", "work" in kwargs, True)


def main():
    for fn in [test_an_in_image_absolute_var_is_not_a_host_path,
               test_a_path_this_build_used_is_refused_even_without_a_shape,
               test_hostish_is_unchanged_outside_vars,
               test_a_refused_recipe_leaves_no_provenance,
               test_both_validation_sites_exempt_vars]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_provenance.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
