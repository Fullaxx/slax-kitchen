# Project status

What is verified, what is written but unsupported, and what does not exist yet. Kept honest on
purpose: a toolkit that overclaims produces broken ISOs nobody notices until boot.

Last updated 2026-09-13.

---

## Verified working

Everything here has been run end to end, and everything marked **booted** was confirmed by booting
the resulting ISO in QEMU and reading the console.

### Core loop

| | Status |
|---|---|
| `kitchen unpack` | ✅ xorriso osirrox, Rock Ridge modes preserved (`bootinst.sh` returns as `0755`) |
| `kitchen pack` | ✅ two backends; defaults to `./out/<source>-custom.iso`, never clobbers without `--force` |
| Round-trip fidelity | ✅ identical size; **19 of 212,819 sectors differ, and all 19 are PVD timestamp fields**. Payload byte-identical, boot-info-table checksum unchanged |
| `kitchen fetch` | ✅ downloads + verifies size and sha256; ordered mirror list with per-mirror verify and fall-through, both paths exercised |
| `kitchen fingerprint` | ✅ ~3.5 s per ISO, mount-free |
| `kitchen probe` | ✅ all four stock ISOs report `MATCH`; classifies differences as benign / explained / unexplained / critical |
| `kitchen doctor` | ✅ reports tool + capability matrix and which recipes this machine can run |
| `kitchen validate` | ✅ JSON Schema, validated post-variable-substitution |
| `kitchen apply` | ✅ dep ordering, `when:` guards, dry run, journalling, pack hints |
| preflight | ✅ every tool/file/capability the plan needs checked up front; `build` checks before unpacking |
| `kitchen build` | ✅ whole pipeline from one profile, ~4 s for four recipes; derives test expectations from the recipe list |
| `kitchen test` | ✅ `--structure` (18 assertions, ~1 s), `--bios`, `--uefi` |

### Recipes

| Recipe | Status |
|---|---|
| `uefi-bootable` | ✅ **booted** — OVMF loads GRUB 2.12 from our ESP, `efifb` comes up, livekit mounts all bundles |
| `isohybrid` | ✅ MBR + GPT + type-`0xEF` partition verified in the image |
| `serial-console` | ✅ entry present in both configs |
| `memtest86plus` | ✅ **booted on BIOS and UEFI** — Memtest86+ 8.10 selected from each menu and running |
| `remove-bundle` | ✅ **booted** — ISO 416 → 336 MiB, five bundles instead of six |
| `remove-chromium` | ✅ named preset for the above; 416 → 336 MiB (Debian), 455 → 340 MiB (Slackware) |
| `rootcopy-overlay` | ✅ **booted** — file copy and preinit hook both confirmed firing |
| `add-packages` | ✅ **booted** on Debian — bundle mounts last, `Live Kit done, starting slax` |

The same ISO boots on **both** BIOS and UEFI after `uefi-bootable` + `isohybrid`, which stock Slax
cannot do at all.

### Quality

10 commit gates, shared by hooks and CI so they cannot drift. Unit tests for the recipe engine's pure
logic run in milliseconds with no ISO. The gates have caught real bugs in their own authors' code
repeatedly — including a `$'\r'` that silently degraded to matching `$r` under `dash`, and a link
checker that only examined one link per line.

---

## Implemented but unsupported

### `bundle.packages` on Slackware

**It works** — it installs, produces a correct 448 KiB bundle, and the ISO boots. It is unsupported
because Slackware has **no dependency resolution** (by design) and the stock mirror is
`slackware64-current`, years ahead of the frozen 2023 base. Neither is fixable from this repo.
Full reasoning: [add-packages cookbook page](../50-cookbook/add-packages.md).

`add-packages` declares `flavours: [debian]`. Everything else works on Slackware normally —
30 of 32 `/slax/boot/` files are byte-identical across flavours.

---

## Rejected, with reasons

| Approach | Why not |
|---|---|
| **`proot` for unprivileged chroot** | **Unsafe.** proot 5.1.0 does not translate `statx()`, so `stat` escapes the fake root and reads the **host** filesystem. Silent and selective — paths that exist on the host appear to work. Could emit a corrupt bundle. Real `chroot` works instead. |
| **`fakechroot`** | LD_PRELOADs host binaries against target libs; Ubuntu 24.04 glibc 2.39 cannot load against Debian 12's 2.36. |
| **byte-identical ISO rebuilds** | No single backend is both faithful and reproducible: `genisoimage` matches upstream but cannot pin dates; `xorriso` pins dates but uppercases the application id. Both ship; pick by task. |

---

## Not implemented yet

### CLI

`shell`, `diff`, `upstream-diff` are declared in `--help` and **error out explicitly** rather than
pretending to work. (`fetch` is implemented and verified — it was listed here in error.)

### Verbs

10 of 25 schema-declared verbs are implemented. A recipe using an unimplemented verb fails with a
clear message naming what *is* available, rather than silently skipping.

**Implemented:** `boot.cmdline` `boot.isohybrid` `boot.menu` `boot.payload` `boot.uefi`
`bundle.packages` `bundle.remove` `iso.files` `rootcopy.files` `rootcopy.preinit`

**Not yet:** `boot.branding` `boot.grub` `boot.secureboot` `bundle.files` `bundle.fromDir`
`bundle.fromTarball` `bundle.renumber` `bundle.script` `initramfs.config` `initramfs.files`
`initramfs.modules` `initramfs.patch` `iso.checksums` `iso.metadata` `kernel.replace`

### Recipes

8 of the ~32 planned. Missing notably: `branding`, `ssh-server`, `boot-tools`,
`initramfs-busybox`, `kernel-replace`.

### Documentation

**Phase 1 is complete.** `00-overview/`, `05-using-slax/`, `10-anatomy/`, `15-upstream/`,
`20-boot-sequence/` and `30-inventory/` are done — 54 pages covering how the ISO is constructed, how
it boots on every medium, what software is in it, and the upstream source of truth for all of it.

`40-workflow/` is also complete — 10 pages, every procedure written to work by hand with the
`kitchen` verb noted alongside.

Still partial, and tracking how far the toolkit itself is built: `50-cookbook/` (an index plus 8 pages, one per
shipped recipe — the other ~24 recipes do not exist yet), `60-testing/`, `70-compat/`,
`90-reference/`. See [the index](../README.md).

---

## Blocked on this machine

The dev container has no `CAP_SYS_ADMIN`, no user namespaces and no `/dev/kvm`. Measured details in
[container vs host](../40-workflow/container-vs-host.md).

| Blocked | Note |
|---|---|
| Tier C boot matrix (BIOS + UEFI + USB + persistence, to desktop) | QEMU works under TCG at ~5–15 min/run; wants KVM to be practical |
| Writing to a real USB device | no block devices |
| Secure Boot / MOK enrolment | needs real firmware |

**Not blocked, contrary to the original plan:** `bundle.packages` needs only `CAP_SYS_CHROOT` and
`CAP_MKNOD`, both of which are present, and does not need `/proc` mounted. `kitchen doctor` now
reports it as available and no longer offers `proot`, which was rejected as unsafe.

So the only genuinely blocked item is the boot matrix, and `--device /dev/kvm` alone fixes it —
`--cap-add SYS_ADMIN` and `--security-opt seccomp=unconfined` are not needed for anything.
→ [host handoff](../40-workflow/host-handoff.md)

---

## Known upstream issues

Twelve, recorded separately in [known upstream issues](../30-inventory/known-upstream-bugs.md).
The two that shape this project most: the stock ISO **cannot boot on UEFI** and **cannot be `dd`'d
to a USB stick**. Both are fixed by recipes here.
