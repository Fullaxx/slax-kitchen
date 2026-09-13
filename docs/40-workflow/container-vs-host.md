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

## The two exceptions

**1. `bundle.packages` (installing distro packages into a bundle)** needs `/proc` mounted inside a
chroot, which `apt` and `dpkg` maintainer scripts rely on. Three backends, tried in order:

1. real `chroot` + bind mounts — needs `CAP_SYS_ADMIN`
2. `unshare -Urm` + mounts inside a user namespace — needs seccomp relaxed
3. **`proot -0 -b /proc -b /sys -b /dev`** — fully unprivileged, works in this container

**2. The Tier C boot matrix** (BIOS + UEFI + USB + persistence, booting all the way to a desktop)
wants KVM to be practical. Note this is a speed problem, not a capability one:

| Tier | What it does | Here |
|---|---|---|
| A — structure | parse the built ISO and assert on it | **fast**, every push |
| B — direct kernel | `qemu -kernel … -initrd …`, exercises the whole livekit init | **works**, ~1–3 min under TCG |
| C — full boot matrix | bootloader → desktop, persistence, USB | works but ~5–15 min/run; belongs on a KVM host |

## Relaxing the container

If you would rather not move machines, these Docker flags unblock both items in place:

```
--device /dev/kvm --security-opt seccomp=unconfined --cap-add SYS_ADMIN
```

`--device /dev/kvm` alone is the single highest-value change: it turns the boot matrix from a
nightly job into something you can iterate on.
