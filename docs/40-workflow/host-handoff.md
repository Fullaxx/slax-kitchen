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
| **Tier C boot matrix** | ⛔ wants KVM. Works under TCG at ~5–15 min/run, which is too slow to iterate on |
| **Busybox gates 4–5** | ⛔ the boot half of the busybox harness, plus a real-hardware `mdev`/`modprobe` bench |
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

### BIOS, optical

```sh
qemu-system-x86_64 -enable-kvm -m 2048 -cdrom out/slax-custom.iso -serial mon:stdio
```

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
cp out/slax-custom.iso /tmp/usb.img
qemu-system-x86_64 -enable-kvm -m 2048 \
  -drive if=none,format=raw,id=u,file=/tmp/usb.img -device usb-storage,drive=u
```

### Persistence

Boot the **same** `usb.img` twice and assert a marker written in run 1 survives into run 2. This is
the hardest exercise of `losetup`, `df` and `date` in the whole system, which is why it is also
busybox gate 4.

Note a `dd`'d hybrid image carries an ISO9660 filesystem, so it is read-only and has **no
persistence of its own** — point `perchdir=` at a second writable device, or test persistence via the
`bootinst` route instead. See [write-to-usb](write-to-usb.md).

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
| TAP output from the in-guest self-test | `tests/boot/golden/` |
| busybox applet list and differential output | `tests/busybox/*.golden` |
| the pinned busybox `.config` | `tests/busybox/` |
| regenerated fingerprints for a new release | `compat/` |
| regenerated manifests | `docs/30-inventory/manifests/` — `ci/gen-manifests.sh isos/slax-*.iso` |

Then push, pull back on the container side, and the structure tier can assert against them for free.

```sh
git add tests/boot/golden && git commit && git push
```

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

This keeps one context and one copy of the task state, which is worth something. It is **untested**
as of this writing — the first connection will need host-key acceptance.
