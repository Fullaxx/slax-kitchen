# Continuous integration

Three workflow files, all driving the **same scripts you can run locally**. Nothing meaningful lives
in the YAML — that is deliberate, so a CI failure is reproducible on a laptop with one command.

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` → `gates` | every push and PR | the thirteen commit gates, ~1 min, no ISOs |
| `ci.yml` → `container` | every push and PR | builds the reference container on **both** `ubuntu:24.04` and `debian:12`, then `doctor --strict` and the gates *inside* each |
| `ci.yml` → `build` | every push and PR | 4-target matrix: fetch, probe, recipe matrix, round-trip |
| `ci.yml` → `boot` | push to master, or a PR labelled `boot-test` | one direct-kernel QEMU boot under TCG, asserting |
| `ci.yml` (weekly) | Thursdays 05:41 UTC, or dispatch | the same, plus the skipped recipes and the [Tier C](tier-c.md) boot matrix |
| `release.yml` | a `v*` tag, or dispatch | guard, then all of `ci.yml` — **the full matrix**, not the per-push subset — then publish |
| `upstream-watch.yml` | Mondays 06:17 UTC, or dispatch | linux-live HEAD, new Slax release, mirror health, pinned signing keys |

## Two cadences, and what is on each

Most of CI runs on every push. Two things deliberately do not, and both are on the weekly run:

| | per push | weekly / tag / dispatch |
|---|---|---|
| gates, container, 4-target build matrix | ✅ | ✅ |
| the recipes in [`ci/slow-recipes.txt`](../../ci/slow-recipes.txt) | ❌ | ✅ |
| direct-kernel boot, asserting markers | ✅ | ✅ |
| BIOS + UEFI screenshot boots | ❌ | ✅ |

**The bar for `ci/slow-recipes.txt` is not "slow".** It is that the recipe's failure mode is
*external* — something outside this repository breaks it — so running it per-push converts someone
else's change into a red master at a cadence nobody can act on. Being merely expensive is not
enough; cost is not a reason to stop checking. Today the file holds one entry: `all-browsers`,
whose four vendor signing keys are pinned by sha256 and will rotate.

Each skip is printed with its reason. A matrix that quietly ran less than it looks like would be
worse than a slow one:

```
  SKIP all-browsers      weekly, not per-push: four sha256-pinned vendor keys are an
                         external dependency (622 s, and a key rotation would redden master)
```

A **release tag runs everything**, because a release should be verified more than a push, not less.
`ci.yml` distinguishes them with `startsWith(github.ref, 'refs/tags/')`.

## The toolchain list

`containers/packages/*.txt` is the only copy. The three install steps in `ci.yml` read those files,
and so does [the reference container](../../containers/README.md) — so a package added for a new
tool reaches CI and a developer's machine in the same commit.

The `container` job is what keeps that honest. `kitchen doctor --strict` exits non-zero if any of
the 20 tools is missing or any toolchain assertion fails, so an incomplete list fails the build
rather than surfacing later as a recipe that cannot find `mcopy`.

It runs on both supported bases. `containers/README.md` claims any current Debian-family release
works; building `ubuntu:24.04` and `debian:12` on every push is what turns that from an assumption
into a tested statement. Both legs are under a minute and run beside the eight-minute build jobs,
so the second one costs nothing.

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
About 40 s per target.

```
recipe matrix: slackware-64bit-15.0.4  (flavour=slackware arch=64bit)
  skip add-packages       not declared compatible with slackware/64bit
  ok   isohybrid          455 MiB
  ok   memtest86plus      454 MiB
  ...
  6 passed, 0 failed, 1 skipped
```

Skips come from each recipe's own `compat` block, so marking something Debian-only is enough — no
separate CI list to keep in sync. **This is what catches a recipe that silently only works on 64-bit
Debian**, which is the easy mistake to make when that is the ISO in front of you.

Assertions are derived from what the recipe claims: `uefi-bootable` is checked for an EFI El Torito
entry, `isohybrid` for a hybrid MBR. A recipe that runs cleanly and changes nothing fails.

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

Modes with no expectations keep the flat sleep: `--bios` and `--uefi` produce an **empty** serial
log, so there is nothing a poll could ever satisfy.

**`--kernel` is the one that can fail.** It boots `vmlinuz` + `initrfs.img` directly with
`console=ttyS0`, bypassing the bootloader, so the whole of livekit init lands in a machine-readable
serial log and is checked for markers:

```
ok   serial contains 'Looking for slax data'
ok   serial contains 'Mounting bundles'
ok   serial contains 'Live Kit done, starting slax'
```

A failure names the stage it stopped at, which is far more useful than "it did not boot". Under TCG
it reaches a `slax login:` prompt in about 150 s.

**`--bios` and `--uefi` cannot assert on serial**, and it is worth understanding why: the default
menu entry carries no `console=ttyS0`, so once the loader hands off, every kernel message goes to
the video console and the serial log stays empty. Those modes prove the *bootloader* works, via a
QMP screenshot — which is the only way to see GRUB or isolinux at all, since they draw to video.

This was a real gap. For four CI runs the boot job reported success while passing no `--expect` at
all, so its only assertion was "a screenshot exists" — a check that could not fail. The BIOS serial
log was 0 bytes and nothing noticed.

## Boot tests are slow, and that is a runner limitation

GitHub runners have no `/dev/kvm`, so QEMU falls back to TCG and a boot to livekit takes minutes
instead of seconds. The boot job is therefore kept to one target and off the per-PR path — add the
`boot-test` label to a PR to opt in. `kitchen test` says which mode it is using rather than
appearing to hang.

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
About 45 seconds for all four with KVM, or 128 s under TCG:

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
tools/qemu/boot-bios.sh out/slax-custom.iso
```

See [QEMU by hand](qemu.md) for the display, the ssh tunnel, and installing to a virtual disk.

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

## Releases

`release.yml` runs on a `v*` tag. Three jobs:

| job | what |
|---|---|
| `guard` | `ci/release-guard.sh` — seconds of shell, ahead of a full-matrix CI run |
| `verify` | `uses: ./.github/workflows/ci.yml` — the whole of it, not a copy |
| `publish` | `gh release create`. **The only job in this repository with `contents: write`.** |

### No ISO is attached, on purpose

[NOTICE.md](../../NOTICE.md) puts the obligations of a built image on whoever publishes it, and part
of the GPLv2 source offer cannot be satisfied from this repository at all: seven prebuilt static
binaries under `vendor/linux-live/initramfs/static/` ship with no in-tree source, and the kernel is
custom-built with an out-of-tree aufs patch set. So a Release carries a tag and an account of what
was verified. You build the ISO.

It also publishes no checksum for a built ISO, because **nobody could check one**: the ISO container
is not byte-reproducible — see [reproducibility](../40-workflow/reproducibility.md). The four base
ISO hashes it does publish are verifiable, and `kitchen fetch` enforces them on every download.

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
