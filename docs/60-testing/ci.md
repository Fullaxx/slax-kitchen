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

`ci/roundtrip.sh` unpacks and repacks with no recipes, then asserts the result is still the same ISO:

```
size      original=435,853,312  rebuilt=435,853,312  delta=+0
sectors   19 of 212,819 differ (0.0089%)
ok        all differences are in the metadata region (<= sector 41)
```

Two assertions matter more than the count. **Identical size**, and **every difference confined to the
ISO9660 metadata region** — those 19 sectors are volume timestamp fields, which no backend can pin
portably. A difference out in the payload would mean a rebuild is corrupting something.

## Base ISOs

They are 416–476 MiB each and are never committed. `kitchen fetch` downloads from the mirror in
`compat/sources.yaml` and verifies **size and sha256** before accepting anything, so a stale or
hostile mirror cannot poison a build. CI caches on the content hash of that file, so each ISO is
downloaded at most once and re-verified on every run.

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
Tracked in `HOST_TASKS.DNC.md`.
