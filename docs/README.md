# slax-kitchen documentation

Slax and Linux Live Kit are the work of **Tomáš Matějíček** — <https://www.slax.org>,
<https://github.com/Tomas-M/linux-live>. This project customizes his work. See
[NOTICE.md](../NOTICE.md).

**[Project status](00-overview/status.md)** — what is verified, what is unsupported, what does not
exist yet. Read this before relying on anything.

## Start here

**I want to customize an ISO.**
[unpack](40-workflow/unpack.md) → [edit bundles](40-workflow/edit-bundles.md) →
[repack](40-workflow/repack-iso.md)

**I want to understand how Slax works.**
[known upstream issues](30-inventory/known-upstream-bugs.md) is the fastest way in — it is written
around the things that are surprising. Then `15-upstream/` for the annotated source of truth.

**I want to contribute.**
[container vs host](40-workflow/container-vs-host.md) — what needs privilege and what does not —
then the commit gates in `ci/checks/`.

## Sections

| | |
|---|---|
| Section | Contents | Status |
|---|---|---|
| `00-overview/` | [Project status](00-overview/status.md) | current |
| `30-inventory/` | Software inventory + [known upstream issues](30-inventory/known-upstream-bugs.md) | partial |
| `40-workflow/` | [unpack](40-workflow/unpack.md), [edit bundles](40-workflow/edit-bundles.md), [repack](40-workflow/repack-iso.md), [container vs host](40-workflow/container-vs-host.md) | partial |
| `70-compat/` | [Fingerprints and `kitchen probe`](70-compat/fingerprints.md) | partial |
| `05-using-slax/` | Using Slax itself: media, persistence, boot modes | planned |
| `10-anatomy/` | How a Slax ISO is put together, component by component | planned |
| `15-upstream/` | `vendor/linux-live` as source of truth, annotated | planned |
| `20-boot-sequence/` | Firmware → loader → kernel → init → chroot, per medium | planned |
| `50-cookbook/` | [add-packages](50-cookbook/add-packages.md), [memtest86plus](50-cookbook/memtest86plus.md), [remove-bundle](50-cookbook/remove-bundle.md), [rootcopy-overlay](50-cookbook/rootcopy-overlay.md) | partial |
| `60-testing/` | QEMU, automated boot tests, CI | planned |
| `90-reference/` | [CLI reference](90-reference/cli.md) + profile format | partial |

## Two things worth knowing up front

**The stock ISO cannot boot on UEFI, and cannot be `dd`'d to a USB stick.** Its El Torito catalog has
a single BIOS entry, and bytes 0–511 are all zero — no MBR, no GPT. The `uefi-bootable` and
`isohybrid` recipes fix both. See [known upstream issues](30-inventory/known-upstream-bugs.md).

**Bundle load order is the numeric prefix, and higher wins.** `union_append_bundles` inserts each
branch at aufs index 1, so each insertion outranks the previous. Stock bundles are `01`–`06` and
`savechanges` writes `99-changes-N.sb`, which is why new bundles land at `07`.

## Supported versions

Slax **12.2.0** (Debian) and **15.0.4** (Slackware), 32- and 64-bit — four targets, all
fingerprinted in `compat/`. These are the newest upstream releases; there has been none since
2023-10-10. `kitchen probe <iso>` tells you what you have.
