#!/usr/bin/env python3
"""ci/md-links.py's slug rules, and that 60-links goes red on a broken anchor.

WHY THIS EXISTS. 60-links.sh checked the half of a link before the `#` and never the half
after it -- `grep -oE '\\]\\([^)#[:space:]]+'`, where the `#` in the negated class threw the
fragment away and made a same-file `[x](#bar)` invisible altogether. So a heading rename
broke every link to it in silence, and five had been wrong for a day: all of them naming
the `sources` heading in docs/90-reference/cli.md, which 9907ed3 grew a `[--strict]` on.
The gate had no test at all before this file.

The slug is the whole gate, and it is where a rewrite goes wrong rather than in the link
scanning. Two of these cases were got wrong in the first draft of ci/md-links.py and
caught here:

  CODE SPANS BEFORE TAGS. `## `sources <iso> ...`` is one code span, so `<iso>` is text.
  Strip html first and `iso` disappears, the slug moves, and the checker reports five
  FALSE breaks while the real ones stay hidden. 240 of this tree's 1041 headings carry a
  code span, so this is the common case and not an edge.

  \\p{Word} IS NOT \\w. GitHub keeps Letter, Mark, Number and Connector_Punctuation.
  Python's `\\w` drops Mark, so it eats the U+FE0F after an emoji that GitHub keeps, and
  9 headings here slug differently between the two.

The rest are shapes this tree actually contains, each with the count that makes it worth a
test: 51 in-fence lines that satisfy the ATX rule, 189 slugs with `--`, 15 ending in `-`,
one duplicate heading, one explicit `<a id>` target, and one link whose text wraps across
two source lines.

The last test drives the REAL gate over a throwaway repository with a planted break, the
way tests/unit/test_ci_lib.py drives 00-no-binaries: a slugger that is right proves
nothing if the gate around it cannot go red.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LIB = os.path.join(ROOT, "ci", "lib.sh")
GATE = os.path.join(ROOT, "ci", "checks", "60-links.sh")
CHECKER = os.path.join(ROOT, "ci", "md-links.py")

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def load():
    """Import ci/md-links.py by path -- the hyphen keeps it off the module namespace."""
    spec = importlib.util.spec_from_file_location("md_links", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args, **kw):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                          text=True, **kw)


def test_a_code_span_keeps_what_looks_like_a_tag():
    """The heading the five broken links named, and the ordering trap inside it."""
    md = load()
    heading = "`sources <iso> [--json F] [--markdown F] [--fetch DIR] [--strict]`"
    check("the real heading's slug", md.slug(heading),
          "sources-iso---json-f---markdown-f---fetch-dir---strict")
    # And the slug those five links carried, so the test states the difference rather
    # than leaving a reader to count hyphens.
    check("...is not the one before --strict was added",
          md.slug("`sources <iso> [--json F] [--markdown F] [--fetch DIR]`"),
          "sources-iso---json-f---markdown-f---fetch-dir")
    check("a tag outside a code span is still stripped", md.slug("a <b>bold</b> claim"),
          "a-bold-claim")


def test_the_character_class_is_categories_and_not_backslash_w():
    md = load()
    # U+FE0F is a Mark: \w drops it, \p{Word} keeps it, and GitHub is the renderer here.
    check("a variation selector survives", md.slug("⚠️ The four things"),
          "️-the-four-things")
    # U+26A0 alone is a symbol and goes, leaving the leading hyphen 6 slugs here have.
    check("a bare symbol leaves a leading hyphen", md.slug("⚠ `LINUX`, not `KERNEL`"),
          "-linux-not-kernel")
    check("an underscore survives as Connector_Punctuation",
          md.slug("5 · `init_blkid_cache`"), "5--init_blkid_cache")


def test_hyphens_are_neither_collapsed_nor_trimmed():
    md = load()
    check("an em dash between words becomes two hyphens", md.slug("Class A — it broke"),
          "class-a--it-broke")
    check("a trailing symbol leaves a trailing hyphen", md.slug("`rootcopy.files` ○"),
          "rootcopyfiles-")
    check("brackets vanish without a separator", md.slug("`boot-host [check|clean|show]`"),
          "boot-host-checkcleanshow")


def test_a_heading_inside_a_fence_is_not_an_anchor():
    md = load()
    doc = ("# Real\n\n"
           "```\n"
           "### TESTKIT BEGIN\n"
           "### TESTKIT END\n"
           "```\n\n"
           "```sh\n"
           "# not a heading either\n"
           "```\n")
    check("only the real heading is an anchor", md.anchors_of(doc), {"real"})


def test_repeats_take_a_numbered_suffix():
    md = load()
    check("the second Recipes is recipes-1",
          md.anchors_of("# Recipes\n\n## Recipes\n"), {"recipes", "recipes-1"})


def test_an_explicit_target_counts_and_a_decoy_does_not():
    md = load()
    doc = ('<a id="drop-then-add"></a>\n\n'
           "## Pair it with a removal\n\n"
           "```\n"
           '<a id="inside-a-fence"></a>\n'
           "```\n")
    check("the explicit target is an anchor",
          "drop-then-add" in md.anchors_of(doc), True)
    check("...and one inside a fence is not",
          "inside-a-fence" in md.anchors_of(doc), False)


def test_a_link_whose_text_wraps_is_still_a_link():
    md = load()
    doc = "see [Pair it with a\nremoval](#drop-then-add). done\n"
    check("the wrapped link is found", [t for _, t in md.links_of(doc)],
          ["#drop-then-add"])
    check("...and on the line it starts on", [n for n, _ in md.links_of(doc)], [1])


def test_what_is_not_this_gate_s_business():
    md = load()
    doc = ("[ext](https://example.invalid/x#frag)\n"
           "[mail](mailto:a@example.invalid)\n"
           "[here](#same-file)\n"
           "```\n[fenced](nope.md#x)\n```\n")
    targets = [t for _, t in md.links_of(doc)]
    check("a fenced link is not a link", "nope.md#x" in targets, False)
    check("a same-file anchor IS one", "#same-file" in targets, True)
    # The external ones are dropped in main() by SCHEME, not by links_of.
    check("an external URL matches the scheme rule",
          bool(md.SCHEME.match("https://example.invalid/x#frag")), True)
    check("a relative path does not",
          bool(md.SCHEME.match("../90-reference/cli.md")), False)


def test_the_gate_goes_red_on_a_broken_anchor():
    """A slugger that is right proves nothing if the gate around it cannot fail."""
    tmp = tempfile.mkdtemp(prefix="mdlinks-")
    try:
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, "ci", "checks"))
        for a in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                  ["config", "user.name", "t"]):
            git(repo, *a, check=True)
        shutil.copy2(LIB, os.path.join(repo, "ci", "lib.sh"))
        shutil.copy2(GATE, os.path.join(repo, "ci", "checks", "60-links.sh"))
        shutil.copy2(CHECKER, os.path.join(repo, "ci", "md-links.py"))
        with open(os.path.join(repo, "target.md"), "w", encoding="utf-8") as fh:
            fh.write("# Target\n\n## A heading that moved\n")
        with open(os.path.join(repo, "pointer.md"), "w", encoding="utf-8") as fh:
            fh.write("# Pointer\n\n"
                     "[stale](target.md#a-heading-that-did-not-move)\n"
                     "[fine](target.md#a-heading-that-moved)\n"
                     "[gone](no-such-file.md)\n")
        git(repo, "add", "-A", check=True)

        p = subprocess.run(["sh", "ci/checks/60-links.sh"], cwd=repo, capture_output=True,
                           text=True, env=dict(os.environ, KITCHEN_SCOPE="tree",
                                               REPO_ROOT=repo, NO_COLOR="1"))
        out = p.stdout + p.stderr
        check("the gate refuses", p.returncode != 0, True)
        check("...naming the stale anchor", "a-heading-that-did-not-move" in out, True)
        check("...and the line it is on", "pointer.md:3" in out, True)
        check("...and the missing file, which is the half it always checked",
              "no-such-file.md" in out, True)
        check("...but not the anchor that resolves",
              "a-heading-that-moved:" in out, False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    for fn in [test_a_code_span_keeps_what_looks_like_a_tag,
               test_the_character_class_is_categories_and_not_backslash_w,
               test_hyphens_are_neither_collapsed_nor_trimmed,
               test_a_heading_inside_a_fence_is_not_an_anchor,
               test_repeats_take_a_numbered_suffix,
               test_an_explicit_target_counts_and_a_decoy_does_not,
               test_a_link_whose_text_wraps_is_still_a_link,
               test_what_is_not_this_gate_s_business,
               test_the_gate_goes_red_on_a_broken_anchor]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_md_links.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
