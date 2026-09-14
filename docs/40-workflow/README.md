# 40 · Workflow

How to actually change a Slax ISO. Where [`10-anatomy/`](../10-anatomy/) explains what the pieces
are, this section is the procedures — each one written so it works by hand, with the `kitchen` verb
that automates it noted alongside.

## The loop

```sh
kitchen unpack isos/slax-64bit-debian-12.2.0.iso
kitchen apply <recipe…>
kitchen pack                       # -> out/slax-...-custom.iso
kitchen test out/*.iso --structure --bios
```

Or `kitchen build <profile>` for all four in one step.

## Pages

**Before you start**

| | |
|---|---|
| [`toolchain.md`](toolchain.md) | every tool, what it is for, and the one-line apt install |
| [`container-vs-host.md`](container-vs-host.md) | what needs privilege — less than you would expect |
| [`host-handoff.md`](host-handoff.md) | the runbook for the one thing that genuinely needs a better machine |

**Changing things** — cheapest first

| | |
|---|---|
| [`unpack.md`](unpack.md) | opening the ISO without losing Rock Ridge modes |
| [`edit-bootloader.md`](edit-bootloader.md) | menus, branding, boot payloads. No rebuild of anything |
| [`edit-bundles.md`](edit-bundles.md) | adding software — the chroot-and-diff technique |
| [`composing-bundles.md`](composing-bundles.md) | splitting work across bundles so the pieces stay independent |
| [`edit-initramfs.md`](edit-initramfs.md) | modules, static binaries, `livekitlib` patches |

**Finishing**

| | |
|---|---|
| [`repack-iso.md`](repack-iso.md) | rebuilding, the two backends, and where the output goes |
| [`write-to-usb.md`](write-to-usb.md) | three routes, and why `dd` is usually the wrong one |
| [`reproducibility.md`](reproducibility.md) | what is deterministic, what is not, and which you need |

## Two things to know before the first edit

**Prefer adding to replacing.** Bundle load order is the numeric prefix and higher wins, so a new
`07-mytools.sb` overrides `01-core.sb` without touching it. That keeps the shipped bundles
byte-identical, keeps `kitchen probe` reporting `MATCH`, and makes the change reversible by deleting
one file. Editing a shipped bundle is for removal only — nothing else needs it.

**The cheapest change is not a bundle at all.** Files under `/slax/rootcopy/` are copied into the
writable layer at boot, before anything starts. No squashfs, no rebuild, and the directory does not
exist on any shipped ISO, so creating it is purely additive.
→ [`rootcopy-overlay`](../50-cookbook/rootcopy-overlay.md)
