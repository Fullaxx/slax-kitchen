# 50 · Cookbook

One page per shipped recipe. Each states what it does, what it measured, and what it cannot do.

**Eight recipes ship today**, all verified by booting the result. The plan lists roughly 32; the
other two thirds do not exist yet, and this index says so rather than implying a fuller shelf than
there is. [Project status](../00-overview/status.md) has the honest ledger.

```sh
kitchen apply <recipe>              # against an unpacked tree
kitchen build <profile>             # fetch -> unpack -> apply* -> pack -> test
```

## Shipped

### Boot and firmware

| | | priv |
|---|---|---|
| [`uefi-bootable`](uefi-bootable.md) | make the ISO boot on UEFI, which the stock ISO **cannot do at all** | ○ |
| [`isohybrid`](isohybrid.md) | add an MBR + GPT so the ISO can be `dd`'d to a stick | ○ |
| [`memtest86plus`](memtest86plus.md) | Memtest86+ 8.10 in the boot menu, BIOS and UEFI | ○ |
| [`serial-console`](serial-console.md) | log the whole boot to a serial port — the prerequisite for automated boot tests | ○ |

The first two fix real gaps in every stock image, and together produce one file that boots four
ways: BIOS optical, UEFI optical, BIOS `dd`'d stick, UEFI `dd`'d stick.

### Software

| | | priv |
|---|---|---|
| [`add-packages`](add-packages.md) | install distro packages into a new `07-*.sb`. The template recipe | ◐ |
| [`rootcopy-overlay`](rootcopy-overlay.md) | drop files straight onto the live filesystem — **no squashfs rebuild at all** | ○ |
| [`remove-bundle`](remove-bundle.md) | drop bundles by regex to slim the image | ○ |
| [`remove-chromium`](remove-chromium.md) | the named preset for the above: −79 MiB Debian, −115 MiB Slackware | ○ |

○ runs anywhere · ◐ needs a real `chroot` (`CAP_SYS_CHROOT` + `CAP_MKNOD`) —
see [container vs host](../40-workflow/container-vs-host.md)

## Which to reach for

Cheapest first. Prefer the first one that solves your problem:

| you want | use | cost |
|---|---|---|
| a config file, a script, an ssh key on the live system | [`rootcopy-overlay`](rootcopy-overlay.md) | **nothing** — copied in at boot |
| extra software | [`add-packages`](add-packages.md) | one `mksquashfs`, ~450 KiB |
| a smaller image | [`remove-chromium`](remove-chromium.md) | one bundle deleted |
| it to boot on a modern laptop | [`uefi-bootable`](uefi-bootable.md) | rebuild |
| it on a USB stick via `dd` | [`isohybrid`](isohybrid.md) + `uefi-bootable` | rebuild |

**Adding beats editing.** Bundle load order is the numeric prefix and higher wins, so a new
`07-mytools.sb` overrides `01-core.sb` without touching it — the shipped bundles stay byte-identical,
`kitchen probe` still reports `MATCH`, and the change is reversible by deleting one file. Editing a
shipped bundle is for **removal only**; nothing else needs it.
→ [bundles-squashfs](../10-anatomy/bundles-squashfs.md)

## Flavour coverage

Seven of the eight work on both flavours and both word sizes. The exception:

| | |
|---|---|
| `add-packages` | **Debian only.** Slackware has no dependency-resolving package manager, and the shipped `slackpkg` configuration points at a dead host (`slackonly.com`, NXDOMAIN) and a mirror that has moved years past the frozen 2023 base. Installing from pinned `.txz` files does work and is the supported route — see the recipe page and [issues 8–9](../30-inventory/known-upstream-bugs.md). |

`ci/recipe-matrix.sh` applies every recipe **individually** to each of the four targets and asserts
on the result, which is what catches a recipe that silently only works on 64-bit Debian.

## Writing your own

A recipe is YAML validated against `schema/recipe.schema.json` before anything runs:

```yaml
apiVersion: slax-kitchen/v1
kind: Recipe
metadata:
  name: my-tools
  summary: Add the tools I always want
compat:
  flavours: [debian]
  arch: [32bit, 64bit]
  privilege: chroot
steps:
  - verb: bundle.packages
    bundle: 07-mytools
    packages: [tmux, ncdu, rsync]
```

- `compat.privilege` is what lets `kitchen doctor` tell you whether this machine can run it.
- `vars:` are substituted as `{{name}}` and can be overridden per profile.
- `when:` guards a step on a fact about the tree, e.g. `flavour==debian`.
- **10 of 25 declared verbs are implemented.** A recipe using an unimplemented one fails with a
  message naming what *is* available. The list is in [the CLI reference](../90-reference/cli.md).

Every recipe needs a page here — `ci/checks/90-doc-coverage.sh` fails the commit otherwise, in both
directions.

## Not written yet

The larger gaps, roughly in the order they would be useful: `branding`, `ssh-server`, `boot-tools`
(`hdt.c32`, `memdisk`, `chain.c32`), `bundle-from-dir`, `initramfs-add-modules`, `firmware-refresh`
(the stock `01-firmware` contains **no GPU firmware** on either flavour), `initramfs-busybox` (the
shipped busybox is from 2017), and `kernel-replace`.
