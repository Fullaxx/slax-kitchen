# Moving work to a privileged host

Most of this toolkit runs anywhere. One thing does not: **the full boot matrix needs `/dev/kvm`**.
This page is the runbook for that — what to carry over, how to run it, and how the results come back
so the unprivileged machine can regression-test against them afterwards.

Run `kitchen doctor` first. If it says `boot tests … KVM (fast)`, you are already on a machine that
can do everything and none of this applies.

## What is actually blocked

Less than the original plan assumed. Two items were expected to need a privileged host and turned out
not to:

| | status |
|---|---|
| `bundle.packages` | ✅ **not blocked** — needs `CAP_SYS_CHROOT` + `CAP_MKNOD` only, not `CAP_SYS_ADMIN`. `apt-get install` completes with exit 0 inside an extracted bundle with no `/proc` mounted |
| Busybox gates 1–3 | ✅ **not blocked** — the shipped i386 static busybox executes directly on an x86-64 host, so applet-parity and output-differential tests are milliseconds, not VM boots |
| **Tier C boot matrix** | ✅ **done** — `ci/tier-c.sh`, four paths, ~25 s a run with KVM. See [Tier C](../60-testing/tier-c.md) |
| Busybox **gate 5** | ✅ **not blocked, and never was** — the rollback proof is seconds and needs no KVM. It was filed here by mislabelling; it runs in CI now |
| Busybox **gate 4** | the boot half — rides on `ci/tier-c.sh` with `initramfs-busybox` applied |
| **A real-hardware `mdev`/`modprobe` bench** | ⛔ genuinely wants hardware, though the PXE third of it is reachable under QEMU with user-mode networking |
| **Writing to a real USB stick** | ⛔ no block devices in a container |
| **Secure Boot / MOK** | ⛔ needs real UEFI firmware; partly manual by nature |

So the handoff is narrower than it sounds: it is a **test** handoff, not a build handoff. Build the
ISO wherever you like; take it somewhere with KVM to prove it boots.

## Option A — relax the container instead

Usually the right answer. One flag:

```sh
docker run --device /dev/kvm …
```

That alone unblocks Tier C and busybox gate 4 in place, and turns a 5–15 minute run into about 30
seconds. `--cap-add SYS_ADMIN` and `--security-opt seccomp=unconfined` are **not** needed — they were
in the original plan only because `bundle.packages` was assumed to need mounts.

Only USB writing and Secure Boot genuinely require leaving.

## Option B — carry the work across

### What syncs, and what does not

```sh
git push origin master        # everything committed
```

**`*.DNC.md` does not travel.** It is gitignored *and* hard-rejected by the `10-no-dnc` commit gate,
deliberately — plan and task state are working files, not project history. Copy them directly:

```sh
# on the host, where both clones are visible
cp /path/to/container-clone/*.DNC.md /path/to/host-clone/
```

**Base ISOs do not travel either** — `isos/` is gitignored. Either `rsync` them or re-fetch:

```sh
./kitchen fetch debian-64bit-12.2.0        # ~416 MiB, sha256-verified
```

Re-fetching is usually faster than copying and is hash-verified on arrival, so prefer it unless the
link is slow.

### Bootstrap the host

```sh
git clone --recurse-submodules <remote> slax-kitchen && cd slax-kitchen
./kitchen doctor --install-hooks
./kitchen doctor                    # must report KVM (fast)
```

`doctor` names the apt package for every missing tool — see [toolchain](toolchain.md) for the full
list in one command.

## The boot matrix

Four targets — `{debian,slackware} × {32,64}bit` — times four boot paths.

```sh
./kitchen build example             # or: unpack / apply / pack
```

> **There are scripts for the first two of these.** `tools/qemu/boot-bios.sh` and
> `tools/qemu/boot-uefi.sh` wrap the commands below and handle the parts they leave to you: picking
> a display backend that this qemu actually has, discovering OVMF, copying its variables file, and
> refusing a UEFI run against an ISO with no EFI entry. See
> [QEMU by hand](../60-testing/qemu.md). The forms below remain the reference.

### BIOS, optical

```sh
qemu-system-x86_64 -enable-kvm -m 2048 -cdrom out/slax-custom.iso -serial mon:stdio
```

Note this passes no display flag, so qemu picks a host-dependent default — on a build without GTK
or SDL that fails outright. The scripts default to VNC for that reason.

### UEFI, optical — needs the `uefi-bootable` recipe

OVMF variables must be a **private writable copy**; the packaged `_VARS` file is read-only and shared.

```sh
cp /usr/share/OVMF/OVMF_VARS_4M.fd /tmp/vars.fd
qemu-system-x86_64 -enable-kvm -m 2048 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=/tmp/vars.fd \
  -cdrom out/slax-custom.iso -serial mon:stdio
```

### USB image — needs the `isohybrid` recipe

```sh
qemu-system-x86_64 -enable-kvm -m 2048 -device qemu-xhci,id=xhci \
  -drive if=none,format=raw,readonly=on,id=u,file=out/slax-custom.iso \
  -device usb-storage,bus=xhci.0,drive=u
```

Three corrections to what this page used to show, all measured:

- **No copy.** It said `cp out/slax-custom.iso /tmp/usb.img` first. An isohybrid ISO *is*
  the USB image, byte for byte — the copy was 440 MiB of litter per run.
- **A controller is required.** Without `-device qemu-xhci` the old line dies with
  `No 'usb-bus' bus found for device 'usb-storage'`. A machine type with no USB
  controller has nothing to plug a stick into.
- **`readonly=on`.** `-drive` opens read-write by default, so the guest held a writable
  handle on the artifact under test. A test that can alter its own input is not a test.

`kitchen test --usb` does all of this for you.

### Persistence

```sh
kitchen test out/slax-custom.iso --persistence
```

Two boots on one disk, asserting that a marker written by the first is there for the
second. This is the hardest exercise of `losetup`, `df` and `date` in the whole system,
which is why it is also busybox gate 4.

A `dd`'d hybrid image carries an ISO9660 filesystem, so it is read-only and has **no
persistence of its own** — the harness attaches a second writable device and points
`perchdir=` at it. Full mechanism in [Tier C](../60-testing/tier-c.md).

### What to assert

| | |
|---|---|
| serial log reaches | `Live Kit done, starting slax` |
| earlier markers | `Looking for slax data`, `Mounting bundles`, `Setting up empty union using aufs` |
| screenshot | Fluxbox desktop, not a blank framebuffer |
| union type | `aufs`, **not** `overlayfs` — the fallback means `aufs.ko` failed to load |

`tests/boot/qemu_boot.py` drives all of this, including QMP screenshots. It needs
`format=png` on `screendump` or you silently get a PPM.

## Getting results back

The point of the handoff is that the unprivileged machine can regression-test afterwards **without**
KVM. So commit the artifacts, not just a verdict:

| produce | commit to |
|---|---|
| the Tier C ledger — one row per boot | `tests/boot/tier-c.json`, written by `ci/tier-c.sh` |
| the testkit block each image produces | `tests/boot/golden/`, one file per image |
| regenerated fingerprints for a new release | `compat/` |
| regenerated manifests | `docs/30-inventory/manifests/` — `ci/gen-manifests.sh isos/slax-*.iso` |

Then push and pull back on the container side. The goldens are not inert: CI's weekly run
diffs against them, so a recipe change that alters the assembled filesystem goes red
without anyone needing KVM.

```sh
git add tests/boot/golden tests/boot/tier-c.json && git commit && git push
```

> **This table used to say "TAP output".** Nothing in this repository has ever produced or
> consumed TAP — the in-guest self-test emits `key: value` lines — and
> `tests/boot/golden/` did not exist. It does now, and so does a gate on what may go in
> it: `ci/checks/97-tier-c-ledger.sh` rejects any string in the ledger that looks like a
> path or a home directory, because results are facts about the artifact and the machine
> that produced them is nobody's business.

Remember to copy the `*.DNC.md` files back too if you ticked anything off on the host — they are the
only state git will not carry for you.

## Driving the host without moving

`ssh`, `scp` and `rsync` are present in the reference container and there is a `Host` entry
configured, so a single session can build locally and run the boot matrix remotely:

```sh
rsync -a out/slax-custom.iso <host>:/tmp/
ssh <host> 'qemu-system-x86_64 -enable-kvm -m 2048 -cdrom /tmp/slax-custom.iso \
            -display none -serial file:/tmp/boot.log & sleep 90; kill %1'
scp <host>:/tmp/boot.log .
```

This keeps one context and one copy of the task state, which is worth something. **It works**:
exercised non-interactively on 2026-09-16 (`ssh -o BatchMode=yes`), driving a full Tier C
run on a remote KVM host from an unprivileged container. The first connection still needs
host-key acceptance.

Two things that pass for free and should not:

- **`git` refuses a repository owned by another user.** If the host account reads a clone
  owned by root, `git describe` fails and anything deriving a version from it silently
  gets an empty string. `ci/tier-c.sh` treats that as fatal now; it did not at first, and
  wrote a ledger whose commit was `"unknown"`.
- **`/usr/sbin` may not be on a non-login ssh `PATH`**, which hides `mkfs.ext4`.
