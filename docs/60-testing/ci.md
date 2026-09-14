# Continuous integration

Three workflows, all driving the **same scripts you can run locally**. Nothing meaningful lives in
the YAML — that is deliberate, so a CI failure is reproducible on a laptop with one command.

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` → `gates` | every push and PR | the nine commit gates, ~1 min, no ISOs |
| `ci.yml` → `build` | every push and PR | 4-target matrix: fetch, probe, recipe matrix, round-trip |
| `ci.yml` → `boot` | push to master, or a PR labelled `boot-test` | QEMU BIOS + UEFI boot under TCG |
| `upstream-watch.yml` | weekly, Mondays | new Slax release, linux-live commits, mirror health |

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

## Boot tests: one asserts, two are evidence

```sh
kitchen test out.iso --kernel --seconds 240      # the assertion
kitchen test out.iso --bios --uefi               # the evidence
```

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

## Upstream watch

Weekly. Compares `linux-live` HEAD and the slax.org changelog against `compat/upstream-baseline.yaml`,
and HEADs every mirror URL checking status *and* size. On a change it opens (or comments on) an
issue labelled `upstream-watch`.

The release comparison is per release line, not a single "latest": Slax publishes two current
releases on the same day — one per flavour — and the changelog carries the full history back to 9.x.
So it flags a higher point release on a line we track, or an entirely higher line, and ignores
history.

## Not in CI yet

The full Tier C matrix — USB image boot and persistence across two boots — needs KVM to be practical.
See [what is blocked on this machine](../00-overview/status.md#blocked-on-this-machine) and
[container vs host](../40-workflow/container-vs-host.md).
