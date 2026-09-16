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


def claim(doc: dict) -> str:
    runs = doc.get("runs", [])
    if not runs:
        return ""
    targets = sorted({r["target"] for r in runs})
    paths = sorted({r["path"] for r in runs})
    failed = [r for r in runs if r.get("result") != "pass"]
    missing = sorted(ALL_TARGETS - set(targets))
    goldens = {r.get("golden") for r in runs}

    out = [
        "**Tier C ran on {}** at commit `{}` on {}, under {} with QEMU {}: {} boots "
        "across {}.".format(
            ", ".join(f"`{t}`" for t in targets), doc["commit"], doc["date"],
            doc["accel"].upper(), doc["qemu"], len(runs), ", ".join(paths))
    ]
    # Only claim the cross-path invariant when every run actually checked it.
    if goldens and goldens <= {"match", "created"}:
        out.append(
            "Every path was asserted on the livekit markers and diffed against one "
            "testkit golden, so all of them assembled an identical filesystem.")
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
