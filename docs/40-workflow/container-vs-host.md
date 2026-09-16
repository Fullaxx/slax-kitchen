# What needs privilege, and what does not

Most of this toolkit runs unprivileged. Exactly two things do not. This page is the measured
capability map, so you can tell at a glance whether the machine in front of you can run a given
recipe.

Run `kitchen doctor` to see the answer for your current machine.

## Measured in the reference dev container

Docker on Ubuntu 24.04, uid 0:

```
CapEff = chown, dac_override, fowner, fsetid, kill, setgid, setuid, setpcap,
         net_bind_service, net_raw, sys_chroot, mknod, audit_write, setfcap
```

| Capability | Present | Consequence |
|---|---|---|
| `mount` (any type) | **no** — no `CAP_SYS_ADMIN` | cannot bind `/proc`, `/sys`, `/dev` into a chroot |
| user namespaces | **no** — blocked by Docker's **seccomp** profile | no `unshare -Urm` workaround |
| `/dev/kvm` | **no** | QEMU runs TCG only, ~10–20× slower |
| loop devices | no | irrelevant — nothing here needs them |
| `/dev/fuse` | no | irrelevant |
| `chroot` | **yes** | can enter a bundle tree |
| `mknod` | **yes** | can build `/dev/{null,zero,urandom,tty}` inside one |

Worth noting the user-namespace block is the *container runtime's*, not the kernel's —
`/proc/sys/user/max_user_namespaces` is 255595 and `unprivileged_userns_clone` is 1. Relaxing
Docker restores it.

## What that leaves working

Everything below is pure userspace file manipulation and runs here with no privileges at all:

- `mksquashfs` / `unsquashfs` — bundle build and unpack
- `cpio` + `xz` — initramfs pack and unpack
- `xorriso` / `genisoimage` / `isohybrid` — ISO read and rebuild
- `mkfs.vfat -C` + `mtools` — **builds the EFI System Partition as a plain file, never mounting
  it.** This is the detail that makes the `uefi-bootable` recipe possible unprivileged.
- `grub-mkstandalone` — produces `BOOTX64.EFI`

That covers 27 of the 32 cookbook recipes, including both flagship ones.

## `bundle.packages` needs less than expected

The original plan assumed it needed `/proc` bind-mounted into a chroot, and therefore
`CAP_SYS_ADMIN`. **That turned out to be wrong.** Measured in this container:

> `apt-get update` and a full `apt-get install` — maintainer scripts and triggers included — both
> complete with **exit 0** inside an extracted `01-core.sb`, with no `/proc` mounted.

The real requirement is `CAP_SYS_CHROOT` + `CAP_MKNOD`, both of which are present. `kitchen doctor`
reports it as available here.

What it does need is for the extracted bundle to be made into a usable root first — Debian's
`01-core.sb` ships **no `/tmp`, `/proc`, `/sys` or `/dev` at all**, so `apt-get update` dies with
`Unable to mkstemp`. `lib/apply.py` creates them (`RUNTIME_DIRS`) and then excludes them from the
finished bundle (`BUNDLE_EXCLUDE`). See [edit-bundles](edit-bundles.md).

### Both unprivileged shortcuts were tested and rejected

| | verdict |
|---|---|
| **`proot`** | ⚠ **unsafe — do not use.** proot 5.1.0 does not translate `statx()`, so `stat` escapes the fake root and reads the **host** filesystem. Proven: it reported `/etc/lsb-release` as present when the bundle has no such file. Silent and selective, so it can emit a corrupt bundle that looks fine. |
| **`fakechroot`** | fails on any glibc mismatch between host and target — it `LD_PRELOAD`s host binaries against target libraries. Measured on the default `ubuntu:24.04` container: glibc 2.39 cannot load against Debian 12's 2.36. A `debian:12` container happens to match the Debian bundles, but not the Slackware ones, and real `chroot` needs neither to agree. |

Real `chroot` works and is the only supported backend. `proot` is no longer listed by
`kitchen doctor` for this reason.

## initramfs recipes need `CAP_MKNOD`

Not `CAP_SYS_ADMIN` — just the ability to create device nodes. `initrfs.img` contains seven of them,
and a `cpio` without the capability silently turns them into **empty regular files**; the repacked
image then cannot open its own console. Both `kitchen doctor` and `apply`'s preflight check for it,
and preflight refuses before anything is unpacked:

```
preflight failed -- 1 unmet requirement(s), nothing has been modified:
  - no mknod capability, needed by initramfs-add-binary
```

Recipes declare this as `compat.privilege: mknod`. In CI, `ci/recipe-matrix.sh` runs any recipe with
a privilege above `none` under `sudo`.

## The one real exception

**The Tier C boot matrix** (BIOS + UEFI + USB + persistence) wants KVM to be practical. Note this is
a speed problem, not a capability one — and the matrix now exists rather than being
described: see [Tier C](../60-testing/tier-c.md).

| Tier | What it does | Here |
|---|---|---|
| A — structure | parse the built ISO and assert on it | **fast**, every push |
| B — direct kernel | `qemu -kernel … -initrd …`, exercises the whole livekit init | **works**, ~1–3 min under TCG |
| C — full boot matrix | bootloader, USB device, persistence across two boots | **implemented**: `ci/tier-c.sh`. ~25 s for four paths with KVM, minutes under TCG |

## Relaxing the container

Only one flag is still worth adding:

```
--device /dev/kvm
```

That turns a 5–15 minute boot-to-desktop run into about 30 seconds — the difference between a
nightly job and something you can iterate on. `--cap-add SYS_ADMIN` and
`--security-opt seccomp=unconfined` are no longer needed for anything, since `bundle.packages`
turned out not to require them.

If you would rather move machines, [host-handoff](host-handoff.md) is the runbook.
