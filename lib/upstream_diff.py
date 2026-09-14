#!/usr/bin/env python3
"""kitchen upstream-diff -- has Linux Live Kit moved under our documentation?

docs/15-upstream/ describes upstream's own source in detail: what `livekitlib`'s 49
functions do, what `initramfs_create` copies, what `build` assembles. Every one of those
pages is a claim about a file in vendor/linux-live at the pinned commit. When upstream
moves, those claims go stale silently -- nothing in the repo changes, no test fails, and
the pages keep asserting yesterday's behaviour.

`ci/upstream-watch.sh` answers "did upstream move?" on a weekly cron. This answers the
next question: "which of the things we wrote down does that actually affect?"

THE MAP COMES FROM THE DOCS. Each page cites its own source path -- livekitlib-reference
names vendor/linux-live/livekitlib, tools.md names vendor/linux-live/tools/. Scraping
those citations means a new doc page is covered the day it is written, and a page that
stops citing a path stops claiming it. A hardcoded table here would be one more thing
to forget to update, which is the exact failure this command exists to catch.
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor", "linux-live")
DOCS = os.path.join(ROOT, "docs")
UPSTREAM_URL = "https://github.com/Tomas-M/linux-live"
CITE = re.compile(r"vendor/linux-live/([A-Za-z0-9._/-]*)")
REPORT = os.path.join(DOCS, "15-upstream", "drift-report.md")


def _git(*args: str, cwd: str = VENDOR) -> tuple:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def doc_claims() -> dict:
    """upstream path -> [doc pages that cite it], scraped from the docs themselves."""
    claims: dict = {}
    for dirpath, _dirnames, filenames in os.walk(DOCS):
        for n in sorted(filenames):
            if not n.endswith(".md"):
                continue
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, ROOT)
            try:
                text = open(full, encoding="utf-8").read()
            except OSError:
                continue
            for m in CITE.finditer(text):
                path = m.group(1).rstrip("/")
                if path:
                    claims.setdefault(path, [])
                    if rel not in claims[path]:
                        claims[path].append(rel)
    return claims


def owners(changed: str, claims: dict) -> list:
    """Doc pages claiming `changed`, by longest matching prefix.

    tools.md cites `tools/`, so it owns every file under it; livekitlib-reference cites
    the single file `livekitlib`. Longest match wins, so a page documenting one file
    inside a directory beats the page documenting the directory.
    """
    best, best_len = [], -1
    for path, pages in claims.items():
        if changed == path or changed.startswith(path + "/"):
            if len(path) > best_len:
                best, best_len = list(pages), len(path)
            elif len(path) == best_len:
                best += [p for p in pages if p not in best]
    return sorted(best)


def resolve(from_ref: str | None, to_ref: str | None, offline: bool) -> tuple:
    if not os.path.isdir(os.path.join(VENDOR, ".git")) and not os.path.isfile(
            os.path.join(VENDOR, ".git")):
        print("vendor/linux-live is not checked out "
              "(git submodule update --init vendor/linux-live)", file=sys.stderr)
        return None, None
    rc, pinned, _ = _git("rev-parse", "HEAD")
    if rc != 0:
        print("cannot read the pinned commit", file=sys.stderr)
        return None, None
    a = from_ref or pinned
    if to_ref:
        return a, to_ref
    if offline:
        return a, "HEAD"
    print(f"  fetching {UPSTREAM_URL} ...")
    rc, _, err = _git("fetch", "--quiet", UPSTREAM_URL, "HEAD")
    if rc != 0:
        print(f"  could not fetch upstream ({err.splitlines()[0] if err else 'no network'});"
              f" comparing against the local checkout instead")
        return a, "HEAD"
    rc, head, _ = _git("rev-parse", "FETCH_HEAD")
    return a, (head if rc == 0 else "HEAD")


def report(a: str, b: str, claims: dict) -> tuple:
    rc, out, err = _git("diff", "--numstat", f"{a}..{b}")
    if rc != 0:
        print(f"  git diff failed: {err.splitlines()[0] if err else '?'}", file=sys.stderr)
        return None, None
    changed = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            add, rm, path = parts
            changed.append((path, add, rm))
    documented, undocumented = [], []
    for path, add, rm in changed:
        pages = owners(path, claims)
        (documented if pages else undocumented).append((path, add, rm, pages))
    return documented, undocumented


def render(a: str, b: str, documented: list, undocumented: list, ncommits: str,
           limit: int = 5) -> list:
    """Group by doc page, ranked by how much moved.

    The action this output exists to prompt is "go re-read that page", so the page is
    the unit, not the file. Forty upstream commits touched 42 files here, most of them
    icons; a flat file list buries `livekitlib +81 -25` -- the one file a 49-function
    reference page describes -- in the middle of six galculator PNGs.
    """
    out = []
    if a == b:
        out.append(f"  pinned at {a[:12]} -- upstream has not moved.")
        return out
    out.append(f"  {a[:12]} -> {b[:12]}"
               + (f"   ({ncommits} commits)" if ncommits else ""))
    out.append("")

    def _n(v):
        return 0 if v == "-" else int(v)

    if documented:
        by_page: dict = {}
        for path, add, rm, pages in documented:
            for pg in pages:
                by_page.setdefault(pg, []).append((path, add, rm))
        ranked = sorted(by_page.items(),
                        key=lambda kv: -sum(_n(a2) + _n(r) for _p, a2, r in kv[1]))
        out.append("  DOCUMENTED AND CHANGED -- these pages assert behaviour that moved:")
        for pg, files in ranked:
            tot = sum(_n(a2) + _n(r) for _p, a2, r in files)
            binary = sum(1 for _p, a2, _r in files if a2 == "-")
            n = len(files)
            note = f"{n} file{'s' if n != 1 else ''}"
            if binary:
                note += f", {binary} binary"
            out.append("")
            out.append(f"    {pg}")
            out.append(f"      {tot} lines across {note}")
            for path, add, rm in sorted(files, key=lambda f: -(_n(f[1]) + _n(f[2])))[:limit]:
                churn = "(binary)" if add == "-" else f"+{add} -{rm}"
                out.append(f"        {path:<52} {churn}")
            if n > limit:
                out.append(f"        ... {n - limit} more")
    else:
        out.append("  Nothing we document changed.")

    if undocumented:
        out.append("")
        n = len(undocumented)
        out.append(f"  changed upstream, not cited by any page here ({n}):")
        for path, add, rm, _ in sorted(
                undocumented, key=lambda f: -(_n(f[1]) + _n(f[2])))[:limit]:
            churn = "(binary)" if add == "-" else f"+{add} -{rm}"
            out.append(f"    {path:<54} {churn}")
        if n > limit:
            out.append(f"    ... {n - limit} more")
    return out


def write_report(lines: list, a: str, b: str) -> None:
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    body = "\n".join(lines)
    with open(REPORT, "w") as f:
        f.write("# Upstream drift report\n\n")
        f.write("Generated by `kitchen upstream-diff --write` -- do not hand-edit.\n\n")
        f.write("`vendor/linux-live` is pinned. When the pointer moves, every page in\n")
        f.write("[15-upstream/](README.md) that cites a changed file is making a claim about\n")
        f.write("code that no longer exists in that form. This lists which ones.\n\n")
        f.write(f"- pinned: `{a}`\n- compared against: `{b}`\n\n")
        f.write("```\n" + body + "\n```\n")
    print(f"  wrote {os.path.relpath(REPORT, ROOT)}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="kitchen upstream-diff",
        description="which docs/15-upstream/ pages does an upstream move invalidate?")
    ap.add_argument("--from", dest="from_ref", help="ref to compare from (default: the pin)")
    ap.add_argument("--to", dest="to_ref", help="ref to compare to (default: upstream HEAD)")
    ap.add_argument("--offline", action="store_true", help="do not fetch")
    ap.add_argument("--write", action="store_true",
                    help="regenerate docs/15-upstream/drift-report.md")
    args = ap.parse_args(argv[1:])

    print("upstream-diff")
    a, b = resolve(args.from_ref, args.to_ref, args.offline)
    if a is None:
        return 2
    rc, sha_b, _ = _git("rev-parse", b)
    if rc == 0:
        b = sha_b
    claims = doc_claims()
    print(f"  {len(claims)} upstream paths cited by docs/")

    documented, undocumented = report(a, b, claims)
    if documented is None:
        return 2
    _rc, ncommits, _ = _git("rev-list", "--count", f"{a}..{b}")
    lines = render(a, b, documented, undocumented, ncommits)
    print("\n".join(lines))
    if args.write:
        write_report(lines, a, b)
    # Exit 1 when something we document moved: this is meant to be runnable in CI.
    return 1 if documented else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
