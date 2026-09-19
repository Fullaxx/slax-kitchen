#!/usr/bin/env python3
"""Turn tests/boot/tier-c.json into the paragraph ci/release-notes.sh prints.

A separate file rather than an inline heredoc because release-notes.sh is itself one
large heredoc, and nesting a second one inside it is how you get a release script that
breaks in a way nobody notices until a tag is pushed.

The paragraph states BOTH halves: what Tier C covered, and what it did not. The half
that matters is the second one -- a reader assumes coverage unless told otherwise, and
this file exists because "Tier C was not run" used to be a constant that would have gone
on printing after it stopped being true.
"""
import json
import sys

ALL_TARGETS = {
    "debian-32bit-12.2.0", "debian-64bit-12.2.0",
    "slackware-32bit-15.0.4", "slackware-64bit-15.0.4",
}


def _field(run: dict, doc: dict, key: str) -> str:
    """A run's own value, or the document's for a row written before runs carried it."""
    value = run.get(key)
    return value if value else doc.get(key, "")


def sittings(doc: dict, runs: list) -> dict:
    """Targets grouped by when and on what they ran: (commit, date, accel, qemu) -> targets.

    FROM THE ROWS, not the document. The ledger merges by target, so its top-level fields
    describe only the last invocation that wrote it -- and this used to print them as if
    they covered every target. Four targets booted at two commits, or on two machines,
    were one sentence naming one of each. Rows carry their own commit and date for exactly
    this reason, and nothing here read them.
    """
    by_target: dict = {}
    for r in runs:
        by_target.setdefault(r["target"], []).append(r)
    groups: dict = {}
    for target in sorted(by_target):
        rs = by_target[target]
        key = (", ".join(sorted({_field(r, doc, "commit") for r in rs})),
               ", ".join(sorted({_field(r, doc, "date") for r in rs})),
               # KVM only if every boot of the target was: one TCG boot is a TCG run.
               "kvm" if all(_field(r, doc, "accel") == "kvm" for r in rs) else "tcg",
               ", ".join(sorted({_field(r, doc, "qemu") for r in rs})))
        groups.setdefault(key, []).append(target)
    return groups


def claim(doc: dict) -> str:
    runs = doc.get("runs", [])
    if not runs:
        return ""
    targets = sorted({r["target"] for r in runs})
    paths = sorted({r["path"] for r in runs})
    failed = [r for r in runs if r.get("result") != "pass"]
    missing = sorted(ALL_TARGETS - set(targets))
    goldens = {r.get("golden") for r in runs}

    groups = sittings(doc, runs)
    if len(groups) == 1:
        (commit, date, accel, qemu), = groups
        out = [
            "**Tier C ran on {}** at commit `{}` on {}, under {} with QEMU {}: {} boots "
            "across {}.".format(
                ", ".join(f"`{t}`" for t in targets), commit, date, accel.upper(), qemu,
                len(runs), ", ".join(paths))
        ]
    else:
        out = [
            "**Tier C ran on {}**, {} boots across {}, in {} separate runs: {}.".format(
                ", ".join(f"`{t}`" for t in targets), len(runs), ", ".join(paths),
                len(groups), "; ".join(
                    "{} at commit `{}` on {}, under {} with QEMU {}".format(
                        ", ".join(f"`{t}`" for t in ts), commit, date, accel.upper(), qemu)
                    for (commit, date, accel, qemu), ts in groups.items()))
        ]
    # Only claim the cross-path invariant when every run actually checked it. When some
    # did not -- a boot that wedges emits no testkit block at all -- say how many did
    # rather than going silent, because silence reads as "none of them were checked",
    # which is a weaker claim than the evidence supports and equally untrue.
    matched = sum(1 for r in runs if r.get("golden") in ("match", "created"))
    if goldens and goldens <= {"match", "created"}:
        out.append(
            "Every path was asserted on the livekit markers and diffed against one "
            "testkit golden, so all of them assembled an identical filesystem.")
    elif matched:
        out.append(
            "{} of the {} were diffed against one testkit golden and matched; the "
            "identical-filesystem claim covers those and not the rest.".format(
                matched, len(runs)))
    if failed:
        out.append("\n\n**{} of those boots FAILED**: {}.".format(
            len(failed), ", ".join(f"`{r['target']}`/{r['path']}" for r in failed)))
    if missing:
        out.append(
            "\n\n**Tier C was not run on {}.** Those targets are matrix-verified, not "
            "boot-verified: they build and their structure is correct; they were not "
            "booted.".format(", ".join(f"`{t}`" for t in missing)))
    out.append(
        "\n\nNo release claims a desktop came up. Tier C asserts that every boot path "
        "reaches `Live Kit done` and assembles the filesystem it should; a person "
        "looking at Fluxbox is `runtime-verified`, which is a different rung.")
    return " ".join(out[:2]) + "".join(out[2:]) if len(out) > 1 else out[0]


def main(argv: list[str]) -> int:
    try:
        doc = json.load(open(argv[1]))
    except Exception as e:                         # noqa: BLE001
        print(f"tier-c-claim: cannot read {argv[1]}: {e}", file=sys.stderr)
        return 1
    text = claim(doc)
    if not text:
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
