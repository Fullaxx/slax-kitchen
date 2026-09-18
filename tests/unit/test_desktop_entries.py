#!/usr/bin/env python3
"""A .desktop this repo writes must survive Slax's launcher generator.

WHY THIS EXISTS. `xlunch_genquick`, in 03-desktop.sb, ends every entry with:

    if [ -e "$Icon" ]; then
       echo "$Name;$Icon;$Exec"
    fi

If its icon search found nothing, `$Icon` is still the bare string from the file, `[ -e wine ]`
is false against the generator's cwd, and the entry is NEVER EMITTED. No error, no log line,
no tile -- the application is simply absent from the launcher. Downstream it cost both
launchers of an image whose entire point was those two launchers, and a green gate suite
never noticed. Issue #17; recorded as issue 15 in docs/30-inventory/known-upstream-bugs.md.

The search that has to fail is narrow: three shapes x three themes x four NUMERIC sizes, with
only ".png" ever appended. So `scalable/` is never searched, `.svg` is never tried, and
`Adwaita/` is not one of the three themes. `Icon=wine` -- the name Debian's own wine package
ships -- resolves to nothing, and so does `accessories-text-editor`, a standard freedesktop
name. Both measured against a built Debian 32-bit stack.

WHAT THIS CAN AND CANNOT SEE. A bare name is decidable here: either the recipe ships a .png
where the generator looks, or the tile vanishes. An ABSOLUTE path into a tree some other verb
fetches -- tor-browser's icon lives inside a 138 MB tarball -- cannot be verified statically,
and this says so rather than pretending. That half is the boot nobody has done yet.

Scope is every place this repo writes a .desktop, including the ```yaml blocks in docs/,
because the stub people copy lives in a doc rather than in a recipe.
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ci"))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import yaml  # noqa: E402

# The fenced-block reader ci/checks/45-doc-yaml.sh already uses. ONE extractor, not two: a
# second copy would drift from the gate's idea of what a documented block is. Loaded by path
# because the filename has a hyphen and is not importable as a module name.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "doc_yaml", os.path.join(ROOT, "ci", "doc-yaml.py"))
_doc_yaml = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_doc_yaml)
fences = _doc_yaml.fences

FAILURES = []

# xlunch_genquick:3-4, copied rather than guessed.
ICON_SIZES = ("128", "64", "48", "32")
ICON_THEMES = ("usr/share/icons/hicolor", "usr/share/pixmaps", "usr/share/icons/gnome")

KEY = re.compile(r"^(Name|Icon|Exec|Hidden|Terminal|NoDisplay)\s*=\s*(.*?)\s*$")


def fail(where, msg):
    FAILURES.append(f"{where}: {msg}")


def parse_desktop(text):
    """The keys, read the way the generator reads them: anchored, last value wins.

    `tac` is what makes it last-wins upstream; the effect is the same either way for a
    well-formed file, and a file with two Icon= lines is worth treating as the generator does.
    """
    out = {}
    for line in text.splitlines():
        m = KEY.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def candidate_paths(icon):
    """The 36 paths xlunch_genquick would try for a bare name, as repo-relative suffixes."""
    paths = []
    for size in ICON_SIZES:
        for theme in ICON_THEMES:
            for shape in (f"{theme}/{size}x{size}/apps/{icon}",
                          f"{theme}/apps/{size}x{size}/{icon}",
                          f"{theme}/{icon}"):
                paths.append(shape + ".png")
                paths.append(shape)
    return paths


def check_entry(where, text, shipped):
    """`shipped` is the set of repo-relative paths the same recipe ships, or None if unknown."""
    keys = parse_desktop(text)
    if not keys:
        return

    # A stub that means to suppress must say so in the key the generator READS. NoDisplay is
    # the freedesktop key and is never parsed by xlunch_genquick, so a stub carrying only
    # NoDisplay suppresses the tile solely because it happens to ship no Icon= -- add one and
    # the entry comes back. Keep NoDisplay for other consumers; require Hidden for this one.
    if keys.get("NoDisplay", "").lower() == "true" and keys.get("Hidden", "").lower() != "true":
        fail(where, "NoDisplay=true without Hidden=true -- xlunch_genquick never reads "
                    "NoDisplay, so this hides the tile only while no Icon= is present. "
                    "Add Hidden=true, which it does read (issue 15)")
        return

    if keys.get("Hidden", "").lower() == "true":
        return                                  # suppressed before the icon search; fine

    icon = keys.get("Icon", "")
    if not icon:
        fail(where, "no Icon= and no Hidden=true -- the generator's final [ -e \"$Icon\" ] "
                    "tests the empty string and drops the entry silently (issue 15)")
        return

    if icon.startswith("/"):
        # Verifiable only when the recipe itself ships the file. When another verb fetches
        # the tree it lands in -- a tarball, a package -- this cannot see it, and says so
        # instead of inventing an answer.
        if shipped is not None:
            rel = icon.lstrip("/")
            parent = os.path.dirname(rel)
            # Only when the recipe ships into THAT EXACT directory: then the file being
            # absent is a typo this can prove. A looser "somewhere under the same top-level
            # directory" test flagged tor-browser, which ships one marker file under
            # opt/tor-browser/Browser/ while its icon arrives three directories away inside
            # a 138 MB tarball -- a false positive, and the kind that teaches people to
            # delete the check.
            ships_into = {os.path.dirname(p) for p in shipped}
            if parent in ships_into and rel not in shipped:
                fail(where, f"Icon={icon} names a file in a directory this recipe ships "
                            f"into, but the file is not among the shipped ones -- the "
                            f"entry would be dropped with no error")
        return

    if shipped is None:
        fail(where, f"Icon={icon} is a bare name in a documented block, and a bare name "
                    f"resolves only if a .png sits in one of 36 places. Use an absolute "
                    f"path in an example, or add Hidden=true (issue 15)")
        return

    if not any(c in shipped for c in candidate_paths(icon)):
        fail(where, f"Icon={icon} resolves to nothing: none of the 36 paths "
                    f"xlunch_genquick searches is shipped by this recipe, so the entry is "
                    f"dropped with no error. Ship usr/share/pixmaps/{icon}.png, or use an "
                    f"absolute path (issue 15)")


def recipe_files(desktop_path):
    """Every repo-relative path inside the `<recipe>.files/` tree that owns this .desktop."""
    parts = desktop_path.split(os.sep)
    for i, seg in enumerate(parts):
        if seg.endswith(".files"):
            top = os.path.join(ROOT, *parts[: i + 1])
            out = set()
            for dirpath, _d, names in os.walk(top):
                for n in names:
                    out.add(os.path.relpath(os.path.join(dirpath, n), top))
            return out
    return set()


def test_desktop_files_shipped_by_recipes():
    """Real .desktop files under recipes/."""
    for dirpath, _d, names in os.walk(os.path.join(ROOT, "recipes")):
        for n in names:
            if not n.endswith(".desktop"):
                continue
            p = os.path.join(dirpath, n)
            rel = os.path.relpath(p, ROOT)
            with open(p, encoding="utf-8") as fh:
                check_entry(rel, fh.read(), recipe_files(rel))


def _walk_steps(doc):
    """Every step-shaped dict, wherever it sits.

    A recipe wraps its steps in `steps:`; a documented block is usually a BARE LIST of them
    (docs/50-cookbook/remove-bundle.md's stub is exactly that). The first version of this
    only looked under `steps:` and silently found nothing in docs/ -- caught by the
    no-inputs assertion at the bottom of this file, which is why that assertion is there.
    """
    if isinstance(doc, dict):
        if "verb" in doc:
            yield doc
        for v in doc.values():
            if isinstance(v, (dict, list)):
                yield from _walk_steps(v)
    elif isinstance(doc, list):
        for item in doc:
            yield from _walk_steps(item)


def _desktop_contents(doc):
    for step in _walk_steps(doc):
        if not isinstance(step, dict):
            continue
        for f in step.get("files") or []:
            if isinstance(f, dict) and str(f.get("dest", "")).endswith(".desktop"):
                if isinstance(f.get("content"), str):
                    yield f["dest"], f["content"]


def test_desktop_written_by_recipe_yaml_and_docs():
    """`content:` blocks that write a .desktop, in recipes AND in documented examples.

    The docs half is the point: the suppression stub people copy lives in
    docs/50-cookbook/remove-bundle.md, not in any recipe, so a check that read only
    recipes/ would have missed the file that propagates the mistake.
    """
    targets = []
    for dirpath, _d, names in os.walk(os.path.join(ROOT, "recipes")):
        for n in names:
            if n.endswith((".yaml", ".yml")):
                p = os.path.join(dirpath, n)
                try:
                    with open(p, encoding="utf-8") as fh:
                        doc = yaml.safe_load(fh.read())
                except yaml.YAMLError:
                    continue
                for dest, content in _desktop_contents(doc):
                    targets.append((f"{os.path.relpath(p, ROOT)} ({dest})", content))

    for dirpath, _d, names in os.walk(os.path.join(ROOT, "docs")):
        for n in names:
            if not n.endswith(".md"):
                continue
            p = os.path.join(dirpath, n)
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            for lineno, block in fences(text):
                try:
                    doc = yaml.safe_load(block)
                except yaml.YAMLError:
                    continue
                for dest, content in _desktop_contents(doc):
                    targets.append((f"{os.path.relpath(p, ROOT)}:{lineno} ({dest})", content))

    # A check with no inputs is a check that cannot fail -- the failure mode this repo has
    # already been bitten by. Assert there is something to look at.
    if not targets:
        FAILURES.append("no .desktop content blocks found at all -- the scan is broken, "
                        "not the tree (a check with no inputs cannot fail)")
    for where, content in targets:
        check_entry(where, content, None)


def main():
    for fn in [test_desktop_files_shipped_by_recipes,
               test_desktop_written_by_recipe_yaml_and_docs]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_desktop_entries.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
