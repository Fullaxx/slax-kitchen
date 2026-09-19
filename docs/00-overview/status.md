# Project status

What is verified, what is written but unsupported, and what does not exist yet. Kept honest on
purpose: a toolkit that overclaims produces broken ISOs nobody notices until boot.

Last updated 2026-09-17.

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
| `kitchen sources` | ✅ every file in a built image matched by sha256 to the stock image or to the step that produced it, from the provenance `pack` writes beside the ISO; exit 1 on anything it cannot account for, 2 on an image it cannot list at all, and `--fetch` gathers the source of what was built |

### Recipes

| Recipe | Status |
|---|---|
| `uefi-bootable` | ✅ **booted** — OVMF loads GRUB from our ESP, `efifb` comes up, livekit mounts all bundles. 2.12 on the default `ubuntu:24.04` container, 2.06 on `debian:12`; both reach a login prompt |
| `isohybrid` | ✅ MBR + GPT + type-`0xEF` partition verified in the image |
| `serial-console` | ✅ entry present in both configs |
| `memtest86plus` | ✅ **booted on BIOS and UEFI** — Memtest86+ 8.10 selected from each menu and running |
| `remove-bundle` | ✅ **booted** — ISO 416 → 336 MiB, five bundles instead of six. The only recipe that removes anything; 455 → 340 MiB on Slackware |
| `rootcopy-overlay` | ✅ **booted** — file copy and preinit hook both confirmed firing |
| `add-packages` | ✅ **booted** on Debian — bundle mounts last, `Live Kit done, starting slax` |
| `initramfs-add-binary` | ✅ **booted** — file survives the repack, image still reaches `slax login:` |
| `initramfs-add-modules` | ✅ **booted** — promotes from a bundle; verified on both flavours and both arches |
| `initramfs-boot-timeout` | ✅ **booted** — `sh -n` gate proven against a deliberately boot-bricking patch |
| `branding` | ✅ **booted** — 4 KiB override bundle beats 01-core; shipped bundles untouched |
| `firmware-refresh` | ✅ **booted** — two bundles above Slax's own (48.8 + 5.4 MiB, ISO 416 → 469 MiB); measured against the 1,959 firmware names the kernel's modules ask for, of which stock has 295 |

The same ISO boots on **both** BIOS and UEFI after `uefi-bootable` + `isohybrid`, which stock Slax
cannot do at all.

### Quality

Thirteen commit gates, shared by hooks and CI so they cannot drift. Unit tests for the recipe engine's pure
logic run with no ISO, in seconds. The gates have caught real bugs in their own authors' code
repeatedly — including a `$'\r'` that silently degraded to matching `$r` under `dash`, and a link
checker that only examined one link per line.

The toolchain is one list — [`containers/packages/`](../../containers/README.md) — read by both the
workflows and the reference container, and CI builds that container on every push and runs
`kitchen doctor --strict` plus all thirteen gates *inside* it. A `v*` tag runs the same CI and then
publishes a Release of the toolkit, whose notes name the rung each target reached. No image is
attached; [NOTICE.md](../../NOTICE.md) sets out what travels with one when it is published. See
[CI](../60-testing/ci.md).

Every image `kitchen pack` writes now carries a provenance record beside it, and `kitchen sources`
turns that into an account of every file in the image. `ci/release-assets.sh` assembles what would
travel with a published one and `ci/release-verify.py` refuses a set that does not carry what it
claims; the weekly CI run proves that pipeline on the `tor` profile and uploads the records and
source, never the image. See [publishing an image](../40-workflow/publishing-images.md).

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
| **`boot.secureboot` verb** | Slax's kernel is custom-built and unsigned, so shim + signed GRUB gets you two links of a three-link chain. Closing the third needs a key we cannot ship (a public private key is theatre), enrolled per-machine through MokManager by a physically present human, on a path we cannot boot-test here. Documented instead: [secure-boot](../20-boot-sequence/secure-boot.md). |
| **`initramfs.config` verb** | Only 2 of the 8 variables in `/lib/config` are read at runtime — `LIVEKITNAME` (14 uses) and `BEXT` (3). The other six are build-time only. And `LIVEKITNAME` is merely the *default* for `from=` (`livekitlib:640`), so a second Live Kit tree on one stick needs no rename at all. Renaming costs the `from=…iso` and PXE paths, and on CD requires re-patching `isolinux.bin`. A verb for one variable nobody should change. |
| **`proot` for unprivileged chroot** | **Unsafe.** proot 5.1.0 does not translate `statx()`, so `stat` escapes the fake root and reads the **host** filesystem. Silent and selective — paths that exist on the host appear to work. Could emit a corrupt bundle. Real `chroot` works instead. |
| **`fakechroot`** | LD_PRELOADs host binaries against target libs, so it fails on any glibc mismatch. Measured on the `ubuntu:24.04` container: 2.39 cannot load against Debian 12's 2.36. |
| **byte-identical ISO rebuilds** | No single backend is both faithful and reproducible: `genisoimage` matches upstream but cannot pin dates; `xorriso` pins dates but uppercases the application id. Both ship; pick by task. |

---

## Not implemented yet

### CLI

Nothing. Every command in `--help` is implemented, and `kitchen status` now reads the journal
`kitchen apply` has always written; see [the CLI reference](../90-reference/cli.md).

### Verbs

23 of 26 schema-declared verbs are implemented. A recipe using an unimplemented verb fails with a
clear message naming what *is* available, rather than silently skipping.

**Implemented:** `boot.cmdline` `boot.isohybrid` `boot.menu` `boot.payload` `boot.uefi`
`bundle.files` `bundle.fromDir` `bundle.fromTarball` `bundle.packages` `bundle.remove`
`bundle.renumber` `bundle.script` `boot.branding` `boot.grub` `initramfs.files`
`initramfs.modules` `initramfs.patch` `initramfs.busybox` `iso.metadata` `iso.checksums`
`iso.files` `rootcopy.files` `rootcopy.preinit` — full reference:
[docs/90-reference/verbs.md](../90-reference/verbs.md)

**Not yet:** `kernel.replace` — the last, and the heaviest
**Won't-do:** `initramfs.config`, `boot.secureboot` — both below

### Recipes

**35.** The remaining gaps are the ones that need real engineering rather than YAML:

| | |
|---|---|
| `boot-tools` | **deferred.** Slax's SYSLINUX modules are Debian stretch's 2017 build, and upstream's own 6.03 binaries fail to load against them — proven in QEMU. See [bootloader-payloads](../10-anatomy/bootloader-payloads.md). |
| `initramfs-helpers` | rebuild the UPX-packed helpers from source via buildroot |
| `kernel-replace` | waits on the `kernel.replace` verb |
| `netboot-export` | emits a PXE tree, not an ISO — a CLI command rather than a recipe |
| persistence | `persistence-preseed` as specified cannot work on read-only media; deferred pending a design conversation |

**Verification is not uniform, and every cookbook page names the rung it reached.** The ladder is
defined in [CONTRIBUTING.md](../../CONTRIBUTING.md); `ci/checks/95-status-vocab.sh` enforces the
vocabulary, which previously did not exist — 23 pages said a bare "verified" that meant six
different things.

| rung | pages |
|---|---|
| `matrix-verified` — builds and passes structure assertions on its declared targets | 14 |
| `artifact boot-verified` — booted, and `testkit` confirms the artifact reached the union | 4 |
| `boot-verified` — booted to `slax login:` with all three livekit markers | 13 |
| `runtime-verified` — the feature was watched working | 4 |

### Documentation

**Phase 1 is complete.** `00-overview/`, `05-using-slax/`, `10-anatomy/`, `15-upstream/`,
`20-boot-sequence/` and `30-inventory/` are done — 56 pages covering how the ISO is constructed, how
it boots on every medium, what software is in it, and the upstream source of truth for all of it.

`40-workflow/` is also complete — 14 pages, every procedure written to work by hand with the
`kitchen` verb noted alongside.

`60-testing/` is complete as of 2026-09-16 — [CI](../60-testing/ci.md) for what runs on every push,
and [QEMU by hand](../60-testing/qemu.md) for booting an image yourself and looking at it.

Still partial, and tracking how far the toolkit itself is built: `50-cookbook/` (an index plus 35
pages, one per shipped recipe), `70-compat/`, `90-reference/` (CLI + all 23 verbs + the profile
format). See [the index](../README.md).

---

## Blocked on this machine

The dev container has no `CAP_SYS_ADMIN`, no user namespaces and no `/dev/kvm`. Measured details in
[container vs host](../40-workflow/container-vs-host.md).

| Blocked | Note |
|---|---|
| Writing to a real USB device | no block devices |
| Secure Boot / MOK enrolment | needs real firmware (OVMF's `secboot` variants make the *negative* proof reachable; enrolment needs a person) |
| A real-hardware `mdev`/`modprobe` bench | wants diverse physical hardware; the PXE third of it is reachable under QEMU |

**No longer blocked.** The Tier C boot matrix — BIOS, UEFI, USB image and persistence —
runs as [`ci/tier-c.sh`](../60-testing/tier-c.md) on any KVM-capable host, about 45 seconds
for four paths, and has been run on **all four targets**: 20 boots, 18 green. The two
failures are persistence boot 2 on both Slackware targets,
[issue #15](https://github.com/Fullaxx/slax-kitchen/issues/15). Busybox gate 5 turned out never to have been blocked at all: it is
the rollback proof, takes seconds, needs no KVM, and had been filed in the host queue by
mislabelling. It runs in CI.

**Not blocked, contrary to the original plan:** `bundle.packages` needs only `CAP_SYS_CHROOT` and
`CAP_MKNOD`, both of which are present, and does not need `/proc` mounted. `kitchen doctor` now
reports it as available and no longer offers `proot`, which was rejected as unsafe.

`--device /dev/kvm` alone would let this container run Tier C in place —
`--cap-add SYS_ADMIN` and `--security-opt seccomp=unconfined` are not needed for anything.
→ [host handoff](../40-workflow/host-handoff.md)

---

## Known upstream issues

Fifteen, recorded separately in [known upstream issues](../30-inventory/known-upstream-bugs.md).
The two that shape this project most: the stock ISO **cannot boot on UEFI** and **cannot be `dd`'d
to a USB stick**. Both are fixed by recipes here.
