# QEMU by hand — validating your own ISO

Booting an image you built and **looking at it**. This is the interactive counterpart to
[CI](ci.md), and the two answer different questions.

`tests/` proves an image did not regress. It boots headless, asserts three livekit markers on a
serial log, captures a PNG, and exits. That is the right shape for something that runs on every
push — and it cannot tell you the desktop came up, the browser launches, the fonts are readable, or
the thing is pleasant to use. Those need a person and a window.

```sh
tools/qemu/boot-bios.sh out/slax-custom.iso     # any Slax ISO
tools/qemu/boot-uefi.sh out/slax-custom.iso     # needs the uefi-bootable recipe
```

## Why VNC is the default

Not a preference — a measurement. On both the dev container and the build host:

```console
$ qemu-system-x86_64 -display help
Available display backend types:
none
curses
```

Debian's `qemu-system-x86` is built without GTK and SDL, and `ldd` on the binary finds zero GUI
libraries. VNC is compiled in separately and works. So on these machines **VNC is the only way to
see a framebuffer at all**, and it happens to be the right answer for a server anyway: the ISO
usually lives where you build it, and VNC tunnels over ssh.

The script prints the tunnel command for you:

```console
$ tools/qemu/boot-bios.sh out/slax-all-browsers-64.iso

  iso       slax-all-browsers-64.iso (1227 MiB)
  firmware  SeaBIOS (legacy)
  accel     KVM
  memory    2048 MiB
  display   vnc on 127.0.0.1:5900 (qemu prints the real port below if taken)

  from your workstation:
    ssh -L 5900:127.0.0.1:5900 bacon
    vncviewer localhost:5900
```

It binds **127.0.0.1** on purpose. An unauthenticated VNC server on a public interface is a remote
console for anyone who finds it; tunnelling costs one flag. Override with `--vnc-addr` only if you
know why. QEMU is given `to=99`, so if 5900 is busy it walks up and prints the port it took — trust
its line over the banner's.

If your QEMU does have a window backend, `--display gtk` or `--display sdl` works. `--display
curses` renders the text console in your terminal and needs no tunnel at all, which is enough to
watch the boot but not to use a desktop.

## What you will see

Slax's menu, with four entries and a 4-second timeout, then a Fluxbox desktop:

| entry | what it does |
|---|---|
| Resume previous session | `perchdir=resume` — the default |
| Start a new session | `perchdir=new` |
| Choose session during startup | `perchdir=ask` |
| Run Slax from RAM | `toram` — copies the whole image into memory first |

**The serial line stays silent, and that is expected.** The terminal you launched from is the QEMU
monitor (`Ctrl-a c` to reach it, `quit` to exit), but no Slax menu entry sets `console=ttyS0` — every
one of them carries `vga=normal` and sends its output to video. That is the same fact that makes
CI's `--bios` and `--uefi` modes screenshot-only; see [CI](ci.md). If you want kernel output on the
serial line, that is [`serial-console`](../50-cookbook/serial-console.md).

## Memory

The default is 2048 MiB, which matches the test harness and is comfortable for a desktop.

**"Run Slax from RAM" needs RAM at least the size of the ISO**, before the running system gets any.
That is fine for a 560 MiB image and a real constraint for a big one:

| ISO | `toram` wants |
|---|---|
| [`debian-browsers`](../50-cookbook/debian-browsers.md), 560 MiB | `--mem 1536` and up |
| [`all-browsers`](../50-cookbook/all-browsers.md), 1227 MiB | `--mem 3072` and up |

Without `toram` the image is read from the virtual CD as it goes, and 2048 is plenty for either.

## UEFI needs the recipe first

`boot-uefi.sh` checks the ISO for a UEFI El Torito entry before starting QEMU, and refuses when
there is none:

```console
$ tools/qemu/boot-uefi.sh out/slax-debian-browsers-64.iso
boot-uefi.sh: slax-debian-browsers-64.iso has no UEFI boot entry.

  El Torito lists BIOS only, so OVMF has nothing to load and you will land in an
  EFI shell. Stock Slax cannot boot on UEFI at all -- that is upstream issue 1.
```

This is worth a check rather than a surprise: without it OVMF starts, finds nothing, and drops to an
EFI shell — which looks like a broken image rather than a missing recipe. Apply
[`uefi-bootable`](../50-cookbook/uefi-bootable.md), or build through a profile that includes it.
`--force` boots anyway.

The check needs `xorriso`. Without it the script says so and continues, rather than passing silently
— "I could not check" and "I checked and it is fine" are different claims.

Firmware is discovered across `/usr/share/OVMF`, `/usr/share/ovmf`, `/usr/share/edk2/ovmf` and
`/usr/share/qemu`; `$OVMF_CODE` and `$OVMF_VARS` override it. The variables file is **copied per
run** — the packaged one is read-only and shared, so writing UEFI variables to it would either fail
or leak state between boots.

## Installing Slax to a virtual disk

Attach a qcow2 and the scripts create it if it is not there:

```sh
tools/qemu/boot-bios.sh out/slax-custom.iso --disk /var/tmp/slax-hd.qcow2 --disk-size 8G
```

It appears in the guest as a plain IDE disk, `/dev/sda`, which is what Slax's installer expects. Boot
the ISO, then follow the `bootinst` procedure in
[write-to-usb](../40-workflow/write-to-usb.md) — partition the disk, make it FAT32, copy `/slax/`
onto it, and run `slax/boot/bootinst.sh` from there. That page is the reference; this one only gets
you a disk to do it to.

Two things about `bootinst.sh` that surprise people: it refuses to run as anything but root, and
when `$DISPLAY` is set it re-execs itself inside a terminal window rather than using the one you
started it from.

Then boot what you installed, with the ISO out of the way:

```sh
tools/qemu/boot-bios.sh out/slax-custom.iso --disk /var/tmp/slax-hd.qcow2 --boot-disk
```

A disk install is also the honest way to exercise persistence, which a `dd`'d hybrid image cannot do
— that carries a read-only ISO9660 filesystem.

## Options

| | |
|---|---|
| `--display vnc\|curses\|gtk\|sdl\|none` | default `vnc`; unknown values warn and try anyway |
| `--vnc-addr ADDR` | default `127.0.0.1` |
| `--mem MiB` | default `2048` |
| `--disk FILE` | attach a qcow2, creating it if absent |
| `--disk-size N` | size for a disk being created, default `8G` |
| `--boot-disk` | boot the disk rather than the ISO |
| `--force` | UEFI only: boot an ISO with no EFI entry |
| `-h`, `--help` | |

KVM is used automatically when `/dev/kvm` is writable, and the banner says which you got. The
difference is large. Measured on the build host with the 1227 MiB `all-browsers` ISO, direct kernel
boot, bisecting the harness budget: **all three livekit markers appear inside 5 seconds** with KVM.
The same image under TCG in an unaccelerated container wants the harness's 150-second budget. Add
the bootloader's own 4-second menu timeout to either figure when booting through the menu, as these
scripts do.

So: with KVM this is an interactive tool. Without it, it is something you start and come back to.

## What these scripts do not do

- **They assert nothing.** Nothing here can fail a build or a commit. If you want an assertion, that
  is `kitchen test --kernel`, documented in [CI](ci.md).
- **They do not replace real hardware.** QEMU tells you the image boots and the desktop works. It
  says nothing about the firmware, GPU or wireless chip in the machine you actually care about — and
  the GPU firmware question in particular is one stock Slax gets wrong.
- **They do not test the USB route.** `dd`ing a hybrid image and booting it as a USB device is its
  own path, with its own QEMU form (`-device usb-storage`); see
  [host-handoff](../40-workflow/host-handoff.md).
