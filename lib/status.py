#!/usr/bin/env python3
"""kitchen status -- what is this work tree, and what has been done to it?

`kitchen apply` has always written `<work>/.kitchen/journal.yaml`, and until now nothing
read it. A record nobody reads is not provenance, it is a file. This is the reader.

The question it answers is the one you have after a break: I unpacked something and
applied some recipes -- which ISO was it, what did I apply, in what order, and what did
that actually change? All three answers are already on disk in .kitchen/; none of them
were reachable.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

B, D, G, Y, O = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    B = D = G = Y = O = ""


def _load(path):
    if not os.path.isfile(path):
        return None
    import yaml
    try:
        return yaml.safe_load(open(path)) or {}
    except Exception:
        return None


def _size(path: str) -> int:
    """A bundle's size, or 0 when it cannot be read. A dangling symlink or a file another
    process removed mid-listing is worth a 0 in a status line, not a traceback."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n} B"


def status(work: str, verbose: bool = False) -> int:
    tree = os.path.join(work, "iso")
    if not os.path.isdir(tree):
        print(f"kitchen status: no work tree at {tree}", file=sys.stderr)
        # A record with no tree beside it: a plain unpack refuses to lay a fresh tree down
        # next to it, so name the command that does.
        if os.path.exists(os.path.join(work, ".kitchen")):
            print(f"  {work}/.kitchen records a tree that is no longer there: "
                  f"`kitchen unpack <iso> -o {work} --force` starts over", file=sys.stderr)
        else:
            print("  run `kitchen unpack <iso>` first", file=sys.stderr)
        return 2

    meta = os.path.join(work, ".kitchen")
    print(f"{B}work tree{O}  {os.path.abspath(work)}")

    # --- where it came from ---------------------------------------------------------
    origin = _load(os.path.join(meta, "origin.yaml"))
    if origin:
        src = origin.get("source_iso", "?")
        print(f"  origin     {os.path.basename(src)}")
        if verbose:
            print(f"             {src}")
            print(f"             sha256 {origin.get('source_sha256', '?')}")
        sz = origin.get("source_size")
        # PyYAML parses an unquoted ISO timestamp into a datetime, so origin.yaml and
        # journal.yaml come back as different types for the same kind of value. Render
        # them identically rather than letting the file format show through.
        when = origin.get("unpacked_at", "?")
        if hasattr(when, "strftime"):
            when = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"             unpacked {when}" + (f", {_human(sz)}" if sz else ""))
    else:
        print(f"  origin     {Y}unknown{O} -- no .kitchen/origin.yaml, so this tree was "
              f"not made by `kitchen unpack`")

    # --- what has been applied ------------------------------------------------------
    journal = _load(os.path.join(meta, "journal.yaml")) or {}
    applied = journal.get("applied") or []
    print()
    if not applied:
        print(f"  {D}nothing applied yet{O}")
    else:
        n = len(applied)
        print(f"  {B}applied{O}    {n} recipe{'s' if n != 1 else ''}, in order")
        for i, entry in enumerate(applied, 1):
            name = entry.get("recipe", "?")
            verbs = entry.get("verbs") or []
            # Collapse repeats: two boot.cmdline steps read better as "boot.cmdline x2"
            counted, seen = [], {}
            for v in verbs:
                if v not in seen:
                    seen[v] = len(counted)
                    counted.append([v, 1])
                else:
                    counted[seen[v]][1] += 1
            vs = ", ".join(v if c == 1 else f"{v} x{c}" for v, c in counted)
            print(f"    {i}. {G}{name}{O}  {D}{entry.get('at', '')}{O}")
            if vs:
                print(f"       {D}{vs}{O}")
            # Vars a profile overrode. Without these, two applications of the same
            # recipe with different values look identical here, and a `kitchen probe`
            # difference has no explanation in the one place that should hold it.
            ov = entry.get("vars") or {}
            if ov:
                print(f"       {D}vars: "
                      + ", ".join(f"{k}={v}" for k, v in sorted(ov.items())) + O)
            for a in entry.get("artifacts") or []:
                mark = f"{Y}-{O}" if a.startswith("-") else "+"
                print(f"       {mark} {a.lstrip('-')}")

    # --- where it came from, inside the recipes ----------------------------------------
    # One line, because the detail is for `kitchen sources` and the sidecar pack writes
    # beside the ISO; this only says whether there is any, so a tree applied before
    # provenance existed is not mistaken for one that fetched nothing.
    prov_path = os.path.join(meta, "provenance.json")
    if applied:
        if os.path.isfile(prov_path):
            import json
            try:
                recs = json.load(open(prov_path)).get("recipes") or []
            except (OSError, ValueError):
                # Unreadable is as informative as malformed here, and `kitchen status`
                # reporting it beats a traceback over a file it only wanted to summarise.
                recs = None
            if recs is None:
                print(f"  {Y}provenance{O} .kitchen/provenance.json is unreadable")
            else:
                steps = [s for r in recs for s in r.get("steps") or []]
                fetched = sum(1 for s in steps if s.get("source", "").startswith(("http://", "https://")))
                fetched += sum(len(s.get("fetched") or []) for s in steps)
                pkgs = sum(len(s.get("installed") or []) for s in steps)
                print(f"  provenance {len(recs)} recipe(s) recorded: {len(steps)} step(s), "
                      f"{fetched} download(s), {pkgs} package version(s)")
        else:
            print(f"  provenance {Y}none recorded{O} -- applied before provenance existed")

    # --- what pack will do ----------------------------------------------------------
    hints = _load(os.path.join(meta, "pack.yaml"))
    if hints:
        print()
        print(f"  {B}pack hints{O} (set by recipes, applied at mastering time)")
        for k in sorted(hints):
            print(f"    {k} = {hints[k]}")

    # --- the tree as it stands now --------------------------------------------------
    mods = os.path.join(tree, "slax", "modules")
    if os.path.isdir(mods):
        sb = sorted(f for f in os.listdir(mods) if f.endswith(".sb"))
        total = sum(_size(os.path.join(mods, f)) for f in sb)
        print()
        print(f"  {B}bundles{O}    {len(sb)}, {_human(total)} total "
              f"{D}(load order: higher wins){O}")
        # Which ones are ours rather than the base image's: anything the journal claims.
        made = {os.path.basename(a) for e in applied for a in (e.get("artifacts") or [])
                if a.endswith(".sb") and not a.startswith("-")}
        for f in sb:
            if f in made:
                tag = f"  {G}<- added here{O}"
            elif f == "98-dpkg-db.sb":
                # Written by pack, not by a recipe, so it is in no journal entry. Say
                # where it came from rather than leaving an unexplained file in the list.
                tag = f"  {D}<- generated by pack (merged dpkg database){O}"
            else:
                tag = ""
            print(f"    {f:<24} {_human(_size(os.path.join(mods, f))):>10}{tag}")

    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="kitchen status",
        description="what this work tree is, and what has been applied to it")
    ap.add_argument("work", nargs="?", default="work",
                    help="work tree (default: ./work)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also show the source path and its sha256")
    a = ap.parse_args(argv[1:])
    return status(a.work, a.verbose)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
