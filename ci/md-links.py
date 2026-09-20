#!/usr/bin/env python3
"""Resolve every internal markdown link, including the half after the `#`.

WHAT WAS WRONG. `ci/checks/60-links.sh` checked the left half of a link and never the
right one. Its extractor was `grep -oE '\\]\\([^)#[:space:]]+'`, and the `#` in that
negated class stopped every target at its fragment: `[x](foo.md#bar)` had `foo.md`
checked and `#bar` discarded, while `[x](#bar)` -- a same-file anchor -- matched nothing
at all and was invisible to the gate entirely.

So a heading rename broke every link to it in silence, and five had been broken for a
day when this was written. All five named `docs/90-reference/cli.md`'s `sources` heading,
which `9907ed3` -- a self-review pass -- grew a `[--strict]` on; the slug moved and
NOTICE.md, publishing-images.md, reproducibility.md, ci.md and verbs.md kept pointing at
where it used to be. The commit that broke them touched cli.md and none of those five,
which is why this is whole-tree by design, like 45-doc-yaml and 95-status-vocab: a
staged-file gate would have passed that commit without a word.

PARSED, NOT GREPPED, and the numbers say why rather than the principle. EVERY COUNT IN THIS
DOCSTRING IS THIS TREE ON 2026-09-20 -- they are the evidence for a design decision, not a
description of the tree as you find it, and an undated one would be read as the second. 51
lines inside fenced code blocks then satisfied the ATX heading rule -- four of them
(`### TESTKIT BEGIN` and `END`, twice each) in *untagged* fences -- so a fence-blind
collector would have invented 51 anchors that do not exist, and a `#testkit-begin-1`
duplicate on top. ee69f5d had just made the same argument about a grepped check that prose
could switch off.

THREE THINGS ABOUT THE SLUG, each of which is the common case here and not an edge:

1. A CODE SPAN CONTRIBUTES ITS LITERAL CONTENTS, and must be resolved BEFORE html tags are
   stripped. `## `sources <iso> [--json F] ...`` is one code span; `<iso>` inside it is
   text, not a tag. Strip tags first and `iso` vanishes, the slug comes out wrong, and
   this reports five FALSE breaks while missing the real ones. 240 of 1041 headings here
   carry a code span. The first draft of this file got the order backwards.

2. THE CHARACTER CLASS IS UNICODE CATEGORIES, not `\\w`. GitHub keeps Letter, Number, Mark
   and Connector_Punctuation; Python's `\\w` excludes Mark, so it drops the U+FE0F after
   an emoji where GitHub keeps it, and 9 headings here slug differently between the two.
   Nothing links to those 9 today, so this changes no current verdict -- which is exactly
   why it is worth writing down now rather than discovering later.

3. HYPHENS ARE NEITHER COLLAPSED NOR TRIMMED. 189 slugs here contain `--`, 15 end in `-`
   and 6 begin with one, and two live links depend on the trailing form
   (`#rootcopyfiles-`, from a heading ending in `○`).

The tree itself is the corpus that proves all of this. On 2026-09-20 it held 55 internal
anchors, 50 of them correct-but-awkward and five broken, and a right implementation reported
exactly those five and nothing else. The five were fixed in the commit that added this file,
so the same run reports none now -- which is why that is dated rather than left to read as a
claim about today. It was true for exactly one commit.

Prints one `path:line<TAB>message` per failure on stdout; ci/checks/60-links.sh turns each
into a fail(). Exit 0 clean, 1 with failures, 2 if this script could not run -- the wrapper
treats 2 as a gate failure, because a checker that dies must not look like one that passed.
"""

import os
import re
import subprocess
import sys
import unicodedata
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
ATX_CLOSING = re.compile(r"[ \t]+#+[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
EXPLICIT = re.compile(r"""<a\s[^>]*?\b(?:id|name)\s*=\s*["']([^"']+)["']""", re.I)
# The link text may span a line break -- one in this tree does -- but never a blank line.
INLINE = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+?)\s*(?:\"[^\"]*\"|'[^']*')?\s*\)")
REFDEF = re.compile(r"^ {0,3}\[[^\]]+\]:[ \t]*(\S+)", re.M)
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def blank_fences(text):
    """Return the text with fenced blocks emptied, line numbering intact.

    Emptied rather than removed: every offset below still maps to the line it came from,
    so a finding can name a line without a second pass.
    """
    out, fence = [], None
    for line in text.split("\n"):
        m = FENCE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                # An opening fence may carry an info string; a closing one may not.
                fence = marker
                out.append("")
                continue
            if marker[0] == fence[0] and len(marker) >= len(fence) and not m.group(2).strip():
                fence = None
            out.append("")
            continue
        out.append("" if fence is not None else line)
    return "\n".join(out)


def rendered(text):
    """The text GitHub renders for a heading, before slugging.

    Code spans keep their literal contents and are taken out of the way FIRST, so that a
    `<iso>` inside one is never mistaken for an html tag. Everything outside them loses
    link syntax, tags and emphasis markers.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "`":
            j = i
            while j < n and text[j] == "`":
                j += 1
            ticks = text[i:j]
            close = text.find(ticks, j)
            if close != -1:
                out.append(text[j:close])
                i = close + len(ticks)
                continue
            out.append(ticks)
            i = j
            continue
        j = text.find("`", i)
        if j == -1:
            j = n
        seg = text[i:j]
        seg = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", seg)
        seg = re.sub(r"!?\[([^\]]*)\]\[[^\]]*\]", r"\1", seg)
        seg = re.sub(r"<[^>]+>", "", seg)
        seg = re.sub(r"(\*\*|__|\*|_|~~)", "", seg)
        out.append(seg)
        i = j
    return "".join(out).strip()


def slug(text):
    """GitHub's TableOfContentsFilter: downcase, drop all but \\p{Word}, `-` and space.

    `\\p{Word}` is Letter, Mark, Number and Connector_Punctuation -- the last is why `_`
    survives, and the second is why a variation selector does. Nothing is inserted where a
    character is dropped, so `f] [--markdown` becomes `f---markdown`.
    """
    keep = []
    for ch in rendered(text).lower():
        cat = unicodedata.category(ch)
        if ch in "- " or cat[0] in "LNM" or cat == "Pc":
            keep.append(ch)
    return "".join(keep).replace(" ", "-")


def anchors_of(text):
    """Every fragment this document answers to, in document order."""
    blanked = blank_fences(text)
    lines = blanked.split("\n")
    found, seen = set(), {}

    def add(s):
        n = seen.get(s, 0)
        seen[s] = n + 1
        found.add(s if n == 0 else f"{s}-{n}")

    for i, line in enumerate(lines):
        m = ATX.match(line)
        if m:
            title = ATX_CLOSING.sub("", m.group(2) or "")
            if title.strip():
                add(slug(title))
            continue
        # Setext: an underline under an ordinary paragraph line. None in this tree, and
        # the rule is kept narrow so a table separator or a thematic break -- which is
        # what `---` is after a BLANK line -- cannot be read as a heading.
        if SETEXT.match(line) and i > 0:
            prev = lines[i - 1]
            if (prev.strip() and not ATX.match(prev) and "|" not in prev
                    and not prev.lstrip().startswith((">", "-", "*", "+"))):
                add(slug(prev.strip()))

    # Explicit targets, read from the blanked text so a decoy inside a fence is not one.
    found.update(EXPLICIT.findall(blanked))
    return found


def links_of(text):
    """(line, target) for every link that points somewhere inside this repository."""
    blanked = blank_fences(text)
    out = []
    for m in INLINE.finditer(blanked):
        if "\n\n" in m.group(0):
            continue
        out.append((blanked.count("\n", 0, m.start()) + 1, m.group(1)))
    for m in REFDEF.finditer(blanked):
        out.append((blanked.count("\n", 0, m.start()) + 1, m.group(1)))
    return out


def main():
    listed = subprocess.run(["git", "-C", ROOT, "ls-files", "*.md"],
                            capture_output=True, text=True)
    if listed.returncode != 0:
        print("md-links: git ls-files failed", file=sys.stderr)
        return 2
    files = sorted(f for f in listed.stdout.split("\n")
                   if f and not f.startswith("vendor/"))

    text_of, anchors = {}, {}
    for rel in files:
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                text_of[rel] = fh.read()
        except OSError as e:
            print(f"md-links: cannot read {rel}: {e}", file=sys.stderr)
            return 2
        anchors[rel] = anchors_of(text_of[rel])

    problems, checked, fragments = [], 0, 0
    for rel in files:
        here = os.path.dirname(rel)
        for line, raw in links_of(text_of[rel]):
            target = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
            # An external URL is somebody else's to answer for, and this gate is offline.
            if SCHEME.match(target):
                continue
            path, _, frag = target.partition("#")
            path, frag = unquote(path), unquote(frag)
            checked += 1
            if path:
                cand = (path.lstrip("/") if path.startswith("/")
                        else os.path.normpath(os.path.join(here, path)))
                # -e, not -f: links to directories and to non-markdown files are ordinary.
                if not os.path.exists(os.path.join(ROOT, cand)):
                    problems.append((rel, line, f"broken internal link: {target}"))
                    continue
            else:
                cand = rel
            if not frag:
                continue
            fragments += 1
            if cand not in anchors:
                # A real file this gate does not parse -- vendored, or not markdown. Its
                # existence was checked above; its headings are not ours to know.
                continue
            if frag not in anchors[cand]:
                where = "in itself" if cand == rel else f"in {cand}"
                problems.append((rel, line, f"no such anchor {where}: #{frag}"))

    for rel, line, msg in problems:
        print(f"{rel}:{line}\t{msg}")
    print(f"md-links: {checked} internal link(s) checked, {fragments} with a fragment, "
          f"across {len(files)} file(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
