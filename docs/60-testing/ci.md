# Continuous integration

Three workflow files, all driving the **same scripts you can run locally**. Nothing meaningful lives
in the YAML — that is deliberate, so a CI failure is reproducible on a laptop with one command.

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` → `gates` | push to master, or any PR | the thirteen commit gates, ~1 min, no ISOs |

**What a test in that job may assume is installed**: `containers/packages/lint.txt` — `shellcheck`,
`yamllint`, `python3-yaml`, `python3-jsonschema` — plus whatever the `ubuntu-24.04` runner image
happens to ship, which today includes `squashfs-tools` and **does not include `xorriso`**. Nothing
else is installed for it. A unit test that needs to read or write an image serves canned output from
a stub and writes the few sectors it needs in Python:
[`test_listing.py`](../../tests/unit/test_listing.py) and
[`test_diff.py`](../../tests/unit/test_diff.py) both do, and say so at the top.

This is not a style preference. `test_diff.py` once built two real ISOs with `xorriso -as mkisofs`,
passed on a developer machine where xorriso is installed, and took the whole gate down in CI with
`FileNotFoundError: 'xorriso'`. To check a test before pushing, run it with a PATH that has no
xorriso on it rather than trusting the machine you are on.
| `ci.yml` → `container` | push to master, or any PR | builds the reference container on **both** `ubuntu:24.04` and `debian:12`, then `doctor --strict` and the gates *inside* each |
| `ci.yml` → `build` | push to master, or any PR | 4-target matrix: fetch, probe, recipe matrix, round-trip |
| `ci.yml` → `boot` | push to master, or a PR labelled `boot-test` | one direct-kernel QEMU boot under TCG, asserting |
| `ci.yml` (weekly) | Thursdays 05:41 UTC, or dispatch | the same, plus the skipped recipes and the [Tier C](tier-c.md) boot matrix |
| `ci.yml` → `tor-assets` | weekly or dispatch — **not on tags** | builds `tor`, assembles and verifies what would travel with it, uploads the records and source — [never the image](#tor-assets-the-pipeline-proven-weekly) |
| `release.yml` | a `v*` tag, or dispatch | guard, then all of `ci.yml` — **the full matrix**, not the per-push subset — then publish |
| `upstream-watch.yml` | Mondays 06:17 UTC, or dispatch | linux-live HEAD, new Slax release, mirror health, pinned signing keys |

## Two cadences, and what is on each

**No push outside `master` triggers anything.** `push` carries `branches: [master]`, so
pushing a working branch runs no CI at all; `schedule` only ever fires on the default
branch; and a `v*` tag is checked by `ci/release-guard.sh` for being an ancestor of
`origin/master` before anything is published.

`pull_request` is deliberately **not** scoped, so opening a PR runs CI whatever it targets.
That is what makes a working branch testable on demand — push it quietly, open a PR when you
want the matrix. It also has to stay unscoped for a PR to be mergeable at all: master's
branch protection requires five checks from this workflow — `commit gates` and the four
`build <target>` jobs — and `pull_request` is the only trigger that produces them for a PR.

Most of CI runs on every push to master. Three things deliberately do not, and all are on the
weekly run:

| | per push | weekly / tag / dispatch |
|---|---|---|
| gates, container, 4-target build matrix | ✅ | ✅ |
| the recipes in [`ci/slow-recipes.txt`](../../ci/slow-recipes.txt) | ❌ | ✅ |
| direct-kernel boot, asserting markers | ✅ | ✅ |
| [Tier C](tier-c.md): build `boot-matrix`, then four paths and five asserting boots | ❌ | ✅ |
| `tor-assets`: the publishing procedure on a real image, never uploading it | ❌ | ✅ weekly and dispatch, ❌ on tags |

**The bar for `ci/slow-recipes.txt` is not "slow".** It is that the recipe's failure mode is
*external* — something outside this repository breaks it — so running it per-push converts someone
else's change into a red master at a cadence nobody can act on. Being merely expensive is not
enough; cost is not a reason to stop checking. Today the file holds three entries, each an
external dependency: `all-browsers`, whose four vendor signing keys are pinned by sha256 and will
rotate; `tor-browser`, a version-pinned 138 MB download; and `firmware-refresh`, 65 sha256-pinned
files fetched from linux-firmware mirrors.

Each skip is printed with its reason. A matrix that quietly ran less than it looks like would be
worse than a slow one:

```
  SKIP all-browsers      weekly, not per-push: four sha256-pinned vendor keys are an
                         external dependency (456 s, and a key rotation would redden master)
```

A **release tag runs everything a push does not**, because a release should be verified more than
a push, not less: the skipped recipes and the Tier C boot matrix both run.
`ci.yml` distinguishes them with `startsWith(github.ref, 'refs/tags/')`.

**`tor-assets` is the one job a tag does not run**, and for the same reason the recipes in
`ci/slow-recipes.txt` are held back: its failure mode is external. `release.yml`'s publish job
needs the whole of `ci.yml`, so on a tag that job would stand between a finished release and
`contents: write`, waiting on a 138 MB download from `dist.torproject.org` that a Tor Browser
release can retire. The weekly run is what proves that pipeline; `workflow_dispatch` runs it on
demand before tagging.

## The toolchain list

`containers/packages/*.txt` is the only copy. The three install steps in `ci.yml` read those files,
and so does [the reference container](../../containers/README.md) — so a package added for a new
tool reaches CI and a developer's machine in the same commit.

The `container` job is what keeps that honest. `kitchen doctor --strict` exits non-zero if any of
its tools is missing or any toolchain assertion fails, so an incomplete list fails the build
rather than surfacing later as a recipe that cannot find `mcopy`.

It runs on both supported bases. `containers/README.md` claims any current Debian-family release
works; building `ubuntu:24.04` and `debian:12` on every push is what turns that from an assumption
into a tested statement. Both legs are under a minute and run beside the eight-minute build jobs,
so the second one costs nothing.

Because those bases move, so do the tools in them — which is half the reason a test here asserts
what *we* do with a tool's output rather than whether the tool works. The rule for writing one, and
what to re-check when a base is bumped, is
[what a test here is for](../../CONTRIBUTING.md#what-a-test-here-is-for).

## Run any of it locally

```sh
./ci/run-checks.sh ci                                          # the gates
./ci/recipe-matrix.sh debian-64bit-12.2.0 isos/slax-64bit-debian-12.2.0.iso
./ci/roundtrip.sh isos/slax-64bit-debian-12.2.0.iso
./ci/upstream-watch.sh
./kitchen build example                                        # what the boot job builds
```

## The recipe matrix is the important one

Every recipe is applied **individually** to each of the four targets, then packed and asserted.
One recipe per work tree, so a failure names exactly one recipe and recipes cannot mask each other.

Measured on this hardware with nothing skipped, `debian-64bit`, 2026-09-17: **33 recipes in 1315 s**,
a median of 6 s each. Four recipes account for more than half of it — `all-browsers` 456 s,
`debian-browsers` 165 s, `libreoffice` 112 s, `firmware-refresh` and `tor-browser` 92 s each. Each
line carries its own seconds, so the numbers above can be re-measured rather than remembered:

```
recipe matrix: debian-64bit-12.2.0  (flavour=debian arch=64bit)
  skip bundle-from-txz    not declared compatible with debian/64bit
  ok   isohybrid          416 MiB, 7 s
  ok   memtest86plus      415 MiB, 7 s
  ...
  33 passed, 0 failed, 2 skipped in 1315 s
```

Skips come from each recipe's own `compat` block, so marking something Debian-only is enough — no
separate CI list to keep in sync. **This is what catches a recipe that silently only works on 64-bit
Debian**, which is the easy mistake to make when that is the ISO in front of you.

Assertions are derived from what the recipe claims: `uefi-bootable` is checked for an EFI El Torito
entry, `isohybrid` for a hybrid MBR. A recipe that runs cleanly and changes nothing fails.

Every image the matrix builds must also pass
[`kitchen sources`](../90-reference/cli.md#sources-iso---json-f---markdown-f---fetch-dir---strict): each
file in it is either byte-identical to the stock image or matches what a recorded step produced.
A verb that writes a file without recording it fails its recipe here, on every target. That is how
`boot.menu` and `boot.branding` were caught writing menus nothing recorded. The matrix builds from a
working tree, so it passes `--allow-dirty`.

## Round-trip guards the core claim

`ci/roundtrip.sh` unpacks, repacks with no recipes, extracts both images again, and compares:

```
size      original=435,853,312  rebuilt=435,853,312  delta=+0
contents  44 entries in the original, 44 in the rebuild
ok        every file byte-identical, every mode preserved
ok        structure preserved (RR=True Joliet=True vol='slax', 2 El Torito entries)
ok        boot-info-table self-consistent (0xe5d3e1ef)
```

It compares **contents, not sector positions**, and the distinction is load-bearing. genisoimage
allocates file extents in directory-scan order, and readdir order varies by filesystem: the first CI
run rebuilt a structurally perfect ISO in which `/slax/boot/EFI/Boot/*` moved from LBA 212257 to
10877, and the old sector-by-sector check scored that 99.9% corrupted. Comparing layout position
tests the build host, not the build. LBA moves are now reported as a note.

The reference side is a **second, pristine extraction of the source** rather than the tree that was
packed. Comparing against the build tree would put any corruption introduced between unpack and pack
on both sides of the comparison — the first draft did exactly that, and a flipped byte and a
stripped exec bit both passed it. Negative-tested since: corrupt a file, strip a mode, delete a
file, and all three fail.

## Base ISOs

They are 416–476 MiB each and are never committed. `kitchen fetch` downloads from the mirror in
`compat/sources.yaml` and verifies **size and sha256** before accepting anything, so a stale or
hostile mirror cannot poison a build. CI caches on the content hash of that file, so each ISO is
downloaded at most once and re-verified on every run.

## Boot tests: one per push, four more weekly

```sh
kitchen test out.iso --kernel --seconds 240              # every push
ci/tier-c.sh                                             # weekly: bios, uefi, usb, persistence
```

**All five assert.** The bootloader boots did not until recently — their serial log was
empty by construction, so the only way either could fail was a zero-byte screenshot that
nothing ever opened, and they reported success for four CI runs while proving nothing.
Two fixes changed that: `kitchen test` now points the bootloader at the entry
[`serial-console`](../50-cookbook/serial-console.md) adds, by reading the ISO's own menu;
and that entry's `console=` order was reversed, because `/dev/console` is the *last* one
and userspace had been writing to the screen. [Tier C](tier-c.md) has both in full.

### `--seconds` is a ceiling, not a bill

It used to be a bill. The harness ran `time.sleep(seconds)` and *then* read the log, so a run cost
its whole budget whatever the guest did — 240 + 120 + 120 = **480 s of sleeping per CI run**, and
the duration it reported back was the flag it had been given rather than anything it measured.

`tests/boot/qemu_boot.py` now polls the serial log and returns as soon as **every** `--expect`
string is present. Measured on the example ISO, under TCG, in a container with no KVM:

```
waited     : 20s of a 240s ceiling (all expectations seen)
```

Same assertions, 20 seconds instead of 240. Two properties make that safe, and both are pinned by
[`tests/unit/test_qemu_boot.py`](../../tests/unit/test_qemu_boot.py):

- **A missing expectation still burns the whole ceiling.** Returning early on a marker that never
  came would report the failure faster but by luck of ordering; waiting as long as we promised is
  what makes "it never appeared" an honest answer.
- **It waits for `all`, not `any`.** `Live Kit done` is the last marker livekit prints, so stopping
  at the first would skip the stages this test exists to cover.

Only a boot with no expectations keeps the flat sleep. That is a menu mode on an image with no
serial entry, or one given `--no-keys`: its serial log stays empty, so there is nothing a poll
could ever satisfy.

**`--kernel` needs no help to assert.** It boots `vmlinuz` + `initrfs.img` directly with
`console=ttyS0`, bypassing the bootloader, so the whole of livekit init lands in a machine-readable
serial log and is checked for markers:

```
ok   serial contains 'Looking for slax data'
ok   serial contains 'Mounting bundles'
ok   serial contains 'Live Kit done, starting slax'
```

A failure names the stage it stopped at, which is far more useful than "it did not boot". Under TCG
it reaches a `slax login:` prompt in about 150 s.

**`--bios`, `--uefi` and `--usb` assert only through the serial entry**, and it is worth
understanding why. A stock menu entry carries no `console=ttyS0`, so once the loader hands off,
every kernel message goes to the video console and the serial log stays empty. So `kitchen test`
reads the ISO's own menu and selects the entry [`serial-console`](../50-cookbook/serial-console.md)
adds.
- **No such entry:** the mode falls back to a QMP screenshot and says so. A screenshot is the only
  way to see GRUB or isolinux at all, since they draw to video.
- **A menu it cannot read:** that is not a menu without the entry. Without `xorriso`, with a menu
  that will not extract, or with an entry whose label cannot be typed at `boot:`, the mode fails,
  boots nothing, and says which.

Both were real gaps. For four CI runs the boot job reported success while passing no `--expect` at
all, so its only assertion was "a screenshot exists", a check that could not fail. The BIOS serial
log was 0 bytes and nothing noticed. Later, a machine without `xorriso` took the same path as an
image without the entry: "no serial entry in this ISO", a screenshot, and a pass. That was the
normal outcome there, and it blamed the image.

## Boot tests are slow, and that is a runner limitation

GitHub runners have no `/dev/kvm`, so QEMU falls back to TCG and a boot to livekit takes tens of
seconds instead of four. The boot job is therefore kept to one target and off the per-PR path — add
the `boot-test` label to a PR to opt in. `kitchen test` says which mode it is using rather than
appearing to hang.

Measured 2026-09-16 at `1463570` on a GitHub runner, the whole boot job. **This table is the only
copy of these figures:**

| step | | |
|---|---|---|
| build the `example` profile | 26 s | per push |
| boot (direct kernel, asserts livekit markers) | **20 s** | per push |
| [Tier C](tier-c.md) — build `boot-matrix`, then four paths, five boots | **2 m 07 s** | weekly |
| | **3 m 21 s** | whole job, weekly |

It was **8 minutes** before the last two passes, of which about 480 seconds was `time.sleep`. The
job is now less than half that *and* runs four more boots, every one of which asserts — the
screenshot modes it replaced could only fail on a zero-byte PNG that nothing ever opened.

Serial logs and screenshots upload as artifacts on every boot run, pass or fail. The screenshot is
not a nicety: **a bootloader menu never reaches the serial log**, because isolinux and GRUB draw to
the video console.

## What CI does not run, and how to run it yourself

CI covers nearly everything. What it cannot do is boot an image the way a person would, because
GitHub-hosted runners have no `/dev/kvm` — TCG works but is 10–20× slower, which is why the boot job
asserts on a serial log rather than looking at a desktop.

Everything below runs on **any KVM-capable Linux host** with `qemu-system-x86_64`, `qemu-img`,
`xorriso` and OVMF. Nothing here is specific to a particular machine.

**The recipes CI skips per-push.** Unset the skip list and the matrix builds everything — this is
exactly what the weekly run does:

```sh
MATRIX_SKIP= ./ci/recipe-matrix.sh debian-64bit-12.2.0 isos/slax-64bit-debian-12.2.0.iso
```

**The Tier C boot matrix** — BIOS, UEFI, USB device and persistence across two boots.
What it costs, on both accelerators, is in [Tier C](tier-c.md), which holds the only copy:

```sh
./kitchen build boot-matrix
./ci/tier-c.sh
```

It writes `tests/boot/tier-c.json` and the testkit goldens, which are what a release's
Tier C claim is derived from. See [Tier C](tier-c.md).

**Boot it and actually look at it.** This is the part CI structurally cannot do, and it is where a
local machine earns its place — the recipe matrix is apt and `mksquashfs` and gains nothing from
virtualisation, but a boot gains everything. Measured: all three livekit markers inside **5 seconds**
with KVM, against a 150-second budget under TCG.

```sh
tools/qemu/boot.py out/slax-custom.iso --bios
```

See [QEMU by hand](qemu.md) for the display, the ssh tunnel, other hardware, disks and
persistence, and `--print` for booting the same image on another machine.

**What is worth committing from such a run** is the *result* — an ISO size, a package count, a boot
marker — because those are facts about the artifact. Facts about the machine that produced them are
not, and do not belong in the repository.

## Upstream watch

**When:** `cron: "17 6 * * 1"` — Mondays 06:17 UTC — plus `workflow_dispatch`.
`ci/upstream-watch.sh` runs standalone too, and takes about a minute.

**Why at all:** upstream is dormant. Slax has shipped nothing since **2023-10-10** and `linux-live`
was last touched **2024-11-14**, so any movement is notable rather than routine — there is no steady
stream of releases to filter. And the things this project pins rot *silently*: `slackonly.com` went
NXDOMAIN and broke `slackpkg` on every stock Slax image, which is the incident the mirror check
exists for. Nothing here fails because of our code; it fails because the world moved.

**What it watches** — four checks, each against a recorded baseline:

| | check | how | baseline |
|---|---|---|---|
| 1 | **`linux-live` HEAD** | `git ls-remote` against Tomáš's repo | `linux_live_head` in [`compat/upstream-baseline.yaml`](../../compat/upstream-baseline.yaml) |
| 2 | **A new Slax release** | the mirror directory listing at `ftp.linux.cz`, cross-checked against `slax.org/changelog.php` | `latest_releases: [12.2.0, 15.0.4]` |
| 3 | **Mirror health** | `curl -sSIL` every mirror × every target | [`compat/sources.yaml`](../../compat/sources.yaml) — HTTP 200 **and** the recorded `Content-Length` |
| 4 | **Pinned signing keys** | fetch each `key_url` and sha256 it | the `key_sha256` in each recipe's `apt.sources` |

Two details worth knowing, because each is a lesson someone already paid for:

**The release comparison is per release line, not a single "latest".** Slax publishes two current
releases on the same day — one per flavour — and the changelog carries the full history back to 9.x.
So neither "the newest entry" nor set membership works: it flags a higher point release on a line we
track, or an entirely new line, and ignores history.

**The mirror check compares size, not just status.** A 200 that returns an error page is exactly the
failure a status-only check waves through.

**Check 4 is why `all-browsers` can safely be weekly.** Its build fails by design when a vendor
rotates a signing key — an unpinned key would let a remote party decide what the image trusts — but
discovering that from a ten-minute build is the expensive way. Fetching four keys and hashing them
takes seconds and names the recipe, the source and both hashes. The pins are parsed out of
`recipes/**/*.yaml`, so a recipe added later is covered without anyone remembering to update the
watch.

**On a change** it opens an issue labelled `upstream-watch` — or comments on the open one rather
than filing a duplicate every week — carrying the adoption procedure: `kitchen probe` each ISO,
regenerate the fingerprint, `kitchen selftest ci`, then fix what breaks. See
[fingerprints](../70-compat/fingerprints.md).

> It does **not** watch busybox or CVEs. The busybox replacement is pinned and tested by
> [`tests/busybox/gates.sh`](../../tests/busybox/gates.sh), not by this.

## Tor assets: the pipeline, proven weekly

The `tor-assets` job runs the [publishing procedure](../40-workflow/publishing-images.md) on a real
profile, so the scripts a project uses to publish are exercised against a real image rather than
fixtures alone:

```sh
sudo ./kitchen build tor
./ci/release-assets.sh out/slax-tor-12.2.0.iso out/tor-assets --no-image
./ci/release-verify.py out/tor-assets --assert-no-images
```

`tor` has what makes the procedure worth testing: a 138 MB prebuilt download with its own
`upstream_source`, Debian packages, and a GRUB EFI image built from the runner's own GRUB, whose
source package is fetched and attached.

**The image is never uploaded.** The Tor Project's trademark policy does not allow "Tor" in the name
of another product without written permission, so this image is built, checked and discarded.
`--no-image` leaves it out of the set; `--assert-no-images` then fails the job if any asset is an
ISO 9660 image, squashfs, ELF, PE or FAT filesystem, judged by content, so a renamed ISO would not
pass either. The upload is a workflow artifact of `out/tor-assets/` only, not a release asset.

Weekly rather than per push for the same reason `tor-browser` is on the slow list: the download is
an external dependency. The verify gate's own logic is unit-tested on every push by
`tests/unit/test_release_assets.py`, which breaks a fixture directory one way at a time.

## Releases

`release.yml` runs on a `v*` tag. Three jobs:

| job | what |
|---|---|
| `guard` | `ci/release-guard.sh` — seconds of shell, ahead of a full-matrix CI run |
| `verify` | `uses: ./.github/workflows/ci.yml` — the whole of it, not a copy |
| `publish` | `gh release create`. **The only job in this repository with `contents: write`.** |

### What a release attaches

A tag and the notes — **no image**. A release of slax-kitchen is the toolkit. What travels with an
image built with it, when one is published, is set out in [NOTICE.md](../../NOTICE.md): the source
of what the build compiled or modified, where each upstream publishes its own, the firmware terms,
and an identity that is not an official Slax release.

This section used to be titled *No ISO is attached, on purpose*, and gave a reason that read a note
written for forks as a rule for this repository. `tests/unit/test_release.py` now checks the notes'
attachment claim against what `release.yml` actually uploads, instead of pinning the sentence: the
day the workflow attaches a file, the notes have to change with it.

A project that does publish an image sets `RELEASE_ASSETS` to the directory
`ci/release-assets.sh` wrote, and the section is generated from that directory by
`ci/redistribution-claim.py` instead, naming what is attached. See
[publishing an image](../40-workflow/publishing-images.md).

There is no checksum for a built image in the notes because none is attached. Where an image *is*
published, its `SHA256SUMS` checks the download — not a rebuild, since the ISO container is not
byte-reproducible; see [reproducibility](../40-workflow/reproducibility.md). The four base ISO hashes
the notes do publish are verifiable, and `kitchen fetch` enforces them on every download.

What it does say is what was *not* done. Tier C — BIOS menu, UEFI, USB image, persistence, boot to a
desktop — needs `/dev/kvm`, which GitHub-hosted runners do not have, so no release claims a desktop
came up. Three of the four targets are matrix-verified and not boot-verified, and the notes say so
in those words.

### Cutting one

```sh
$EDITOR kitchen                       # KITCHEN_VERSION: 0.1.0-dev -> 0.1.0
./ci/release-guard.sh v0.1.0          # must pass before you tag
git commit -am 'Release 0.1.0' && git push
git tag v0.1.0 && git push --tags     # -> guard, full CI, Release published
$EDITOR kitchen                       # -> 0.2.0-dev, commit
```

`0.x` tags are published as prereleases: nothing here promises a stable interface yet.

`ci/release-notes.sh v0.1.0` prints the body to stdout, so you can read it before any of this.

### Rehearsing it

A tag push happens once and cannot be undone by pushing another one, so the workflow is exercised
before it matters. Run it from the Actions tab with **Run workflow**, `tag: v0.1.0-dev`, and
**publish** left off. That runs the guard and the whole of CI for real, prints the notes, and
publishes nothing.

This is why `release-guard.sh` splits its rules. "The tag matches `kitchen:8`" is always enforced;
"a released version has no `-dev` suffix" applies only to a real tag push. Without the split, a
rehearsal would have to skip the guard — and a rehearsal that skips the thing being rehearsed is
not one.

The guard also refuses to treat *could not check* as *checked*. Its third rule compares the commit
against `origin/master`; on a shallow checkout that ref is absent, and it fails a tag push rather
than passing. That failure mode is why `actions/checkout` there uses `fetch-depth: 0`.

## Action versions are a Node 24 floor

GitHub removes the Node 20 runtime from Actions runners on **2026-09-23**; runners have defaulted to
Node 24 since **2026-06-16**. A JavaScript action whose `action.yml` declares `runs.using: node20`
stops working on that date ([changelog][node24]).

The versions pinned in `ci.yml` are therefore a floor, not a preference. Each was checked against
its own `action.yml` at the tag rather than assumed:

| action | last node20 | first node24 | pinned |
|---|---|---|---|
| `actions/checkout` | v4 | **v5** | v7 |
| `actions/cache` | v4 | **v5** | v6 |
| `actions/upload-artifact` | **v5** | **v6** | v7 |
| `actions/github-script` | v7 | **v8** | v9 |

**`upload-artifact` v5 is still node20** — one major up from v4 would not have fixed it, which is
why each was verified individually. Do not drop any of these below the "first node24" column.

`github-script` v9 is ESM: `require('@actions/github')` fails, and a script declaring
`const getOctokit` is a `SyntaxError`. Our `upstream-watch` script does neither and uses only
`require('fs')`, which is unaffected. `cache` v5+ needs runner ≥ 2.327.1, which matters only for
self-hosted runners; we use GitHub-hosted `ubuntu-24.04`.

`ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION=true` would keep node20 working until 2026-09-23. We are
not using it — the point of the upgrade is to not need it.

**There is no row here for a release-upload action**, because `release.yml` does not use one. `gh` is
preinstalled on the runner, has no Node runtime to age out of support, and is one less program
fetched by a movable tag and run as root on the machine that builds the ISO.

[node24]: https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/

## Not in CI, and why

**A desktop.** Tier C asserts that every boot path reaches `Live Kit done` and assembles
the filesystem it should. Whether Fluxbox came up is `runtime-verified`, and it needs a
person and a window — [QEMU by hand](qemu.md).

**The committed Tier C evidence.** GitHub-hosted runners have no `/dev/kvm`, so CI runs
the four paths under TCG to prove the harness still works, and writes its ledger to a
scratch path. `tests/boot/tier-c.json` comes from a KVM host. What CI *does* own is the
golden diff — a recipe change that alters the assembled filesystem turns the weekly run
red against the committed block.

**Real hardware, and Secure Boot enrolment.** See
[what is blocked on this machine](../00-overview/status.md#blocked-on-this-machine) and
[container vs host](../40-workflow/container-vs-host.md).
