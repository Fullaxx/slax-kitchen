# slax-kitchen documentation

Slax and Linux Live Kit are the work of **Tomáš Matějíček** — <https://www.slax.org>,
<https://github.com/Tomas-M/linux-live>. This project customizes his work. See
[NOTICE.md](../NOTICE.md).

**[Project status](00-overview/status.md)** — what is verified, what is unsupported, what does not
exist yet. Read this before relying on anything.

New to Slax? [What Slax is](00-overview/what-is-slax.md) in one page, and the
[glossary](00-overview/glossary.md) for the vocabulary.

## Start here

**I just want to use Slax.**
[quick-start](05-using-slax/quick-start.md) → [persistence](05-using-slax/persistence-perch.md) →
[boot modes](05-using-slax/boot-modes-and-tricks.md)

**I want to customize an ISO.**
[toolchain](40-workflow/toolchain.md) → [unpack](40-workflow/unpack.md) →
[edit bundles](40-workflow/edit-bundles.md) → [repack](40-workflow/repack-iso.md) →
[write to USB](40-workflow/write-to-usb.md), with [the cookbook](50-cookbook/) for worked recipes.
If you are splitting work across several bundles — several browsers, say — read
[composing bundles](40-workflow/composing-bundles.md) first. If you are forking this to build
your own image, [recipes in a fork](40-workflow/recipes-in-a-fork.md) is the one to read.

**I want to understand how Slax works.**
[runtime-layout](10-anatomy/runtime-layout.md) first — nearly every "where does my file go?" question
resolves to a path under `/run/initramfs/memory/`. Then
[livekit-init](10-anatomy/livekit-init.md) for what `/init` does, and
[bundles-squashfs](10-anatomy/bundles-squashfs.md) for why higher numbers win.

**I want to know why something surprised me.**
[known upstream issues](30-inventory/known-upstream-bugs.md) — twelve of them, each one a thing that
will otherwise cost you an afternoon.

**I want a claim's source.**
[15-upstream](15-upstream/) cites a file and line in the pinned `vendor/linux-live` for everything;
[10-anatomy](10-anatomy/) shows the command that measures it on a real ISO.

**I want to contribute.**
[container vs host](40-workflow/container-vs-host.md) — what needs privilege and what does not — then
the commit gates in `ci/checks/`. [`containers/`](../containers/README.md) builds the reference
container, which is the environment the measurements in these pages were taken on.

## Sections

| Section | Contents | Status |
|---|---|---|
| `00-overview/` | [what Slax is](00-overview/what-is-slax.md) · [glossary](00-overview/glossary.md) · [scope](00-overview/scope-and-non-goals.md) · [status](00-overview/status.md) | **current** |
| `05-using-slax/` | [Using Slax itself](05-using-slax/): media, persistence, boot modes, everyday tasks | **current** |
| `10-anatomy/` | [How a Slax ISO is put together](10-anatomy/), measured from the bytes | **current** |
| `15-upstream/` | [`vendor/linux-live` as source of truth](15-upstream/), annotated | **current** |
| `20-boot-sequence/` | [Firmware → loader → kernel → init → chroot](20-boot-sequence/), per medium | **current** |
| `30-inventory/` | [Software inventory](30-inventory/) + [known upstream issues](30-inventory/known-upstream-bugs.md) | **current** |
| `40-workflow/` | [How to change an ISO](40-workflow/): tools, unpack, edit, repack, write to USB | **current** |
| `50-cookbook/` | [29 recipes](50-cookbook/), incl. [uefi-bootable](50-cookbook/uefi-bootable.md) and [firmware-refresh](50-cookbook/firmware-refresh.md) | good |
| `60-testing/` | [CI](60-testing/ci.md); QEMU and boot tests | partial |
| `70-compat/` | [Fingerprints and `kitchen probe`](70-compat/fingerprints.md) | partial |
| `90-reference/` | [CLI](90-reference/cli.md) · [all 23 verbs](90-reference/verbs.md) · profile format | partial |

**The first seven sections are complete** — Slax itself (how to use it, what is in it, how it is
built, how it boots) plus the procedures for changing it. The remaining four describe this toolkit's
own surface, and are written as far as the toolkit is built.

## Three things worth knowing up front

**The stock ISO cannot boot on UEFI, and cannot be `dd`'d to a USB stick.** Its El Torito catalog has
a single BIOS entry, and the whole 32 KiB system area is zero — no MBR, no GPT. The `uefi-bootable`
and `isohybrid` recipes fix both. → [the UEFI gap](20-boot-sequence/uefi-cd-gap.md)

**Bundle load order is the numeric prefix, and higher wins.** `union_append_bundles` inserts each
branch at aufs index 1, so each insertion outranks the previous. `00`–`09` is the platform,
`10`–`89` is yours, and `98`/`99` are refused —
[which numbers are whose](10-anatomy/bundles-squashfs.md#which-numbers-are-whose).

**The two flavours' bundles are shaped differently.** Debian's `01-core` has no `/dev`, `/proc` or
`/tmp` at all; Slackware's is a full FHS tree with 7,323 device nodes. Neither is a usable chroot as
shipped, and the runtime directories are created at boot by `change_root` instead. This is the fact
most likely to catch you out. → [bundles-squashfs](10-anatomy/bundles-squashfs.md)

## Supported versions

Slax **12.2.0** (Debian) and **15.0.4** (Slackware), 32- and 64-bit — four targets, all
fingerprinted in `compat/`. These are the newest upstream releases; there has been none since
2023-10-10. `kitchen probe <iso>` tells you what you have.
