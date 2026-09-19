# QEMU by hand — validating your own ISO

Booting an image you built and **looking at it**. This is the interactive counterpart to [CI](ci.md), and the two answer different questions.

`tests/` proves an image did not regress. It boots headless through four paths — BIOS, UEFI, a USB
device and twice onto one disk for persistence — asserts three livekit markers on each serial log,
diffs the testkit block against a committed golden, and exits. See [Tier C](tier-c.md). That is the right shape for something that runs on every
push — and it cannot tell you the desktop came up, the browser launches, the fonts are readable, or
the thing is pleasant to use. Those need a person and a window.

```sh
tools/qemu/boot.py out/slax-custom.iso --bios                    # any Slax ISO
tools/qemu/boot.py out/slax-custom.iso --uefi                    # needs the uefi-bootable recipe
tools/qemu/boot.py out/slax-custom.iso --uefi --print > run.sh   # the same boot, for another machine
```

[`tools/qemu/boot.py`](../../tools/qemu/boot.py) is one Python file. `--bios` or `--uefi` is the
only choice you have to make; everything else has a default, and [the options table](#options) lists
all of it. It replaced `boot-bios.sh` and `boot-uefi.sh`.

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

The launcher prints the tunnel command for you. Captured in the dev container, which has no KVM:

```console
$ tools/qemu/boot.py out/slax-boot-matrix-debian-64bit-12.2.0.iso --bios

  iso       slax-boot-matrix-debian-64bit-12.2.0.iso (422 MiB), CD-ROM on IDE
  machine   pc, qemu-system-x86_64, 2 cpus
  memory    2048 MiB
  firmware  SeaBIOS (legacy)
  accel     TCG (no writable /dev/kvm -- 10-20x slower)
  network   e1000, user-mode
  display   vnc on 127.0.0.1:5900  (default)

  from your workstation:
    ssh -L 5900:127.0.0.1:5900 <kvm-host>
    vncviewer localhost:5900

  the terminal is the qemu monitor and the guest's serial line: Ctrl-a c switches,
  Ctrl-a x quits, and Ctrl-C goes to the guest, not to qemu.
  the serial line stays silent after the bootloader: no Slax menu entry sets console=ttyS0
  unless the serial-console recipe added one.

Could not access KVM kernel module: No such file or directory
qemu-system-x86_64: -accel kvm: failed to initialize kvm: No such file or directory
qemu-system-x86_64: falling back to tcg
VNC server running on 127.0.0.1:5900
```

The three KVM lines are qemu's, and they are expected where there is no `/dev/kvm`: the launcher
asks for `-accel kvm -accel tcg` and lets qemu fall back, rather than deciding for it. With KVM they
do not appear.

It binds **127.0.0.1** on purpose. An unauthenticated VNC server on a public interface is a remote
console for anyone who finds it; tunnelling costs one flag. `--vnc-addr IP[:PORT]` overrides it, and
anything outside `127.0.0.0/8`, `::1`, `10/8`, `172.16/12`, `192.168/16` and `fc00::/7` is **refused
unless you also pass `--vnc-public`** — after which every launch says `PUBLIC ADDRESS` out loud.
An address that is private but routable, such as a docker bridge or a LAN address, is accepted
without ceremony and the banner tells you to connect to it directly rather than to build a tunnel
to somewhere you can already reach.

**The port is the one you asked for, and there is no walk-up.** QEMU used to be given `to=99`, so a
busy 5900 became 5901 and qemu printed the port it took. That is a good answer for someone reading
the terminal and a wrong one for everything else: with a [boot host](boot-host.md) the ssh tunnel is
built for one port *before* qemu starts, so a walk-up leaves the viewer connected to a tunnel that
reaches nothing — or, on a second launch, reaches the previous boot, which looks like it worked. A
busy port now fails and says which one.

`--vnc-addr` takes a port, not a display number, and refuses one below 5900: qemu's VNC server
listens on `5900 + display`, so `-vnc 127.0.0.1:5900` would bind port 11800. The conversion happens
where qemu is called, once.

**IPv6 works, and must be bracketed** — `[::1]:5900`, `[fd00::1]:5902`. An unbracketed literal is
refused rather than guessed at, because `fe80::1:5900` reads as "`fe80::1`, port 5900" to a person
and is *also* a valid address in its own right: accepting it bare would silently bind a different
machine. `::1` and `fc00::/7` count as private; a link-local `fe80::` address does not, because
reaching one needs a zone index this does not carry, so it asks for `--vnc-public` like any other
address it cannot vouch for. Verified end to end against a KVM host: qemu bound `[::1]:5900`, the
tunnel carried it, and `vncviewer localhost:5900` reached the guest.

## Booting on a machine that has KVM

With a [`boot-host.ini`](boot-host.md), the launcher runs qemu **there** and brings the window
here. The image is sent (once per content), the guest starts on that machine, and the VNC server it
binds is reached over an ssh tunnel that lasts exactly as long as the session:

```console
$ tools/qemu/boot.py out/slax-boot-matrix-debian-64bit-12.2.0.iso --bios
  slax-boot-matrix-debian-64bit-12.2.0.iso -> kvmbox  (kvmbox, qemu 8.2.2, KVM)
  sent 301 files (image already cached)

  vncviewer localhost:5900  (tunnelled to 127.0.0.1:5900 on kvmbox for this session)
```

The terminal is still the qemu monitor — `-tt` gives the far side a pty, so `Ctrl-a c`, `Ctrl-a x`
and `Ctrl-C` behave exactly as they do locally. `--local` boots here instead.

**The guest does not outlive the launcher.** Two connections are used: a control session that owns
the run directory and sends a heartbeat, and the interactive one carrying the terminal and the
tunnel. Killing the launcher — with `SIGTERM` or with `SIGKILL` — takes down the local listener, the
remote `boot.py` and the guest, and removes the run directory. Both were measured; before the
control session learned to kill by run directory rather than only by pid file, a `SIGKILL` left a
qemu running out of a directory that had already been deleted.

A relative `--disk` path lands in `<scratch>/boot-host/disks/` on that machine, which
`kitchen boot-host clean` never touches. Paths other than the image are paths over there.

Starting a boot on a port already in use is refused **before anything is sent**, in about a tenth of
a second, naming the port and suggesting the next one. That is not only about a second launch: the
dev container this was built in already runs its own VNC desktop on 5901, and a viewer pointed at a
port somebody else owns answers `RFB 003.008` quite happily — it looks like it worked.

With `vnc`, `gtk` or `sdl` the pointer is a USB tablet (`--no-tablet` removes it, `--tablet` adds it
to the others). A VNC client sends absolute positions, which a tablet takes as they are; through a
relative PS/2 mouse the guest's cursor drifts away from yours.

If your QEMU does have a window backend, `--display gtk` or `--display sdl` works; the launcher warns,
and tries anyway, when this qemu does not list the one you asked for. `--display curses` renders the
text console in your terminal and needs no tunnel at all, which is enough to watch the boot but not
to use a desktop.

## What you will see

A boot menu, then a Fluxbox desktop. Which menu depends on how you booted:

| boot | menu | entries |
|---|---|---|
| `--bios`, the ISO as a CD or a stick | isolinux, **hidden**: the splash and a 4-second countdown; `Esc` shows the list, `Tab` edits a command line | *Run Slax from CD* (the default), *Run Slax from RAM*, and two session entries greyed out, because a CD cannot hold a session |
| `--uefi` | GRUB, as [`uefi-bootable`](../50-cookbook/uefi-bootable.md) builds it, 5-second timeout unless the recipe was given another | the same two, plus *Reboot* and *Power off* |
| `--boot-disk`, after installing with `bootinst` | SYSLINUX from the disk, same splash and countdown | *Resume previous session* (the default), *Start a new session*, *Choose session during startup*, *Run Slax from RAM* |

[bios-cd](../20-boot-sequence/bios-cd.md) and [bios-usb-hdd](../20-boot-sequence/bios-usb-hdd.md)
walk through the two SYSLINUX menus. Entries that recipes add, such as `serial-console`'s, appear in
all of them. *Run Slax from RAM* copies the whole image into memory first — see [Memory](#memory-and-cpus).

**By default the terminal you launched from is the QEMU monitor and the guest's serial line at
once.** `Ctrl-a c` switches between them, `Ctrl-a x` quits, and `Ctrl-C` is passed to the guest
rather than stopping qemu. `--serial` moves the serial line elsewhere; see
[Serial and monitor](#serial-and-monitor).

**The serial line stays silent after the bootloader, and that is expected.** No stock Slax menu entry
sets `console=ttyS0` — every one of them carries `vga=normal` and sends its output to video. Under
`--uefi` you do see something first: OVMF mirrors its console there, so its `BdsDxe: starting Boot0001`
line and GRUB's whole menu arrive before it goes quiet. To keep the kernel and livekit on the serial
line, apply [`serial-console`](../50-cookbook/serial-console.md) and choose its entry, or boot with
[`--kernel-boot`](#booting-the-kernel-directly). CI's bootloader modes solve the same problem the first
way: they select the entry `serial-console` adds, so they assert on images that carry it and fall
back to screenshot evidence on images that do not — see [CI](ci.md).

## Firmware: `--bios` or `--uefi`

There is no default, deliberately. The two boot different loaders from different parts of the ISO —
isolinux through the BIOS El Torito entry, GRUB through the EFI one — and which of them you meant to
look at is not something to guess.

### UEFI needs the recipe first

The launcher checks the ISO for a UEFI El Torito entry before starting QEMU, and refuses when there
is none:

```console
$ tools/qemu/boot.py isos/slax-64bit-debian-12.2.0.iso --uefi
boot.py: slax-64bit-debian-12.2.0.iso has no UEFI boot entry.

  El Torito lists BIOS only, so OVMF has nothing to load and you will land in an
  EFI shell. Stock Slax cannot boot on UEFI at all -- upstream bug 1 in
  docs/30-inventory/known-upstream-bugs.md.

  Fix it by applying the uefi-bootable recipe, or by building through a profile
  that already includes it:

      ./kitchen apply uefi-bootable -w work && ./kitchen pack -s work/iso
      ./kitchen build browsers

  Boot it anyway with --force, or use --bios instead.
```

This is worth a check rather than a surprise: without it OVMF starts, finds nothing, and drops to an
EFI shell — which looks like a broken image rather than a missing recipe. Apply
[`uefi-bootable`](../50-cookbook/uefi-bootable.md), or build through a profile that includes it.

It checks one level deeper, too. Having found the EFI boot image, it looks inside it for the
loader the firmware will ask for — `EFI/BOOT/BOOTX64.EFI` for `--arch x64`, `BOOTIA32.EFI` for
`--arch x86` — and refuses when it is missing. And an image attached with `--iso-bus usb` is a disk,
not a CD, so there the check reads the partition table for an EFI system partition instead of El
Torito. `--force` boots past any of these, and says which.

The checks read the ISO directly with `lib/isoparse.py`, so they need no `xorriso`. If the EFI image
cannot be parsed the launcher says it could not check, and boots — "I could not check" and "I checked
and it is fine" are different claims.

### Firmware files

For `--bios` QEMU brings SeaBIOS itself. For `--uefi`, the firmware is the first of: `--ovmf-code`
and `--ovmf-vars`; then `$OVMF_CODE` and `$OVMF_VARS`; then a search the same as
`tests/boot/qemu_boot.py`'s — `/usr/share/OVMF`, `/usr/share/ovmf`, `/usr/share/edk2/ovmf`,
`/usr/share/qemu`, in that order — and `tests/unit/test_tools_qemu.py` fails if the two lists ever
differ. For `--arch x86` the variables are `$OVMF32_CODE` and `$OVMF32_VARS`, so a 64-bit override
left exported cannot load 64-bit firmware into a 32-bit machine. `--print` never searches; it uses
fixed paths instead, for the reason [below](#printing-instead-of-booting---print).

The variables file is **copied per run**: the packaged one is read-only and shared, so writing UEFI
variables to it would either fail or leak state between boots. The copy lives in a scratch
directory, with anything else the run needs, and is removed when QEMU exits — including when the
launcher is sent SIGTERM or SIGHUP, which it passes on to QEMU. (The shell scripts this replaced
set a trap to delete their copy and then `exec`ed QEMU, which discards the trap; every UEFI run left
one behind.) Under `--print` the copy is made beside the ISO instead, and stays.

Firmware built for Secure Boot — `OVMF_CODE_4M.secboot.fd`, and `.ms.fd` and `.snakeoil.fd`, which
are symlinks to it — aborts without SMM, and runs only on `q35`: the distribution's own firmware
descriptors in `/usr/share/qemu/firmware/` say `requires-smm` and list q35 machines alone. Pointing
`--ovmf-code` at one selects `-machine q35,smm=on` and the secure flash setting, and refuses an
explicit `--machine pc`.

## Architecture: `--arch x64` or `x86`

`x64`, the default, runs `qemu-system-x86_64`. `x86` runs `qemu-system-i386`, a machine with no
64-bit mode at all. A 32-bit Slax ISO boots under either — Tier C runs all four targets under
`qemu-system-x86_64` — so `x86` is for asking what a genuinely 32-bit machine does with it.
Measured under TCG: the 32-bit boot-matrix image reaches `Live Kit done` on `qemu-system-i386` both
through isolinux and with `--kernel-boot`.

**32-bit UEFI cannot boot anything this repository builds.** It needs `ovmf-ia32`, which no package
list here installs, and a `BOOTIA32.EFI` in the image, which nothing here makes:
[`uefi-bootable`](../50-cookbook/uefi-bootable.md) installs `BOOTX64.EFI` only. So `--arch x86
--uefi` is refused twice over: without `ovmf-ia32` a launch stops there, naming the package, and
with it — or under `--print` — for the missing loader, which `--force` boots past into an EFI shell.
Debian 12's `ovmf-ia32` ships only the Secure Boot build, which needs `q35` with SMM — so that is
what a launch there selects, and what `--print` always uses.

## Machine and storage buses

`--machine pc` (the default) is i440FX with PIIX IDE, the machine every boot test in this repository
has used. `--machine q35` is ICH9, with AHCI in the chipset.

`--iso-bus` says how the ISO is attached and `--disk-bus` does the same for every `--disk`. Both
default to the machine's own controller: `ide` on `pc`, `sata` on `q35` — except under `--uefi
--kernel-boot`, where they default to `sata`, for the reason [below](#booting-the-kernel-directly).

| bus | on `pc` | on `q35` | guest driver | measured, TCG |
|---|---|---|---|---|
| `ide` | PIIX IDE: the CD where `-cdrom` would put it, disks from the primary master | refused | `ata_piix`, built in | ISO and disk |
| `sata` | an added `ahci` controller | the chipset's own AHCI ports | `ahci`, built in | ISO on both machines, disk on `pc` |
| `scsi` | `virtio-scsi-pci` | the same | `virtio_scsi`, built in | ISO and disk |
| `usb` | `qemu-xhci`; the ISO read-only, as a stick | the same | `xhci-pci` and `usb-storage`, initramfs modules | ISO and disk |
| `virtio` | `virtio-blk-pci` — disks only | the same | `virtio_blk`, built in | disk |
| `nvme` | `nvme` — disks only | the same | `nvme`, built in | disk |

"Built in" is read from `modules.builtin` inside the initramfs of all four stock images, and the two
USB modules are in the initramfs too, which is why an ISO on any of these buses is found: livekit
searches every block device for its data, and every driver it needs is there before it looks. The
one exception is IDE under `--uefi --kernel-boot`, [below](#booting-the-kernel-directly).

"Measured" means, for the ISO: with `--bios --kernel-boot` livekit found its data on the bus
(`/dev/sr0` for the three CD buses, `/dev/sda1` for the stick); OVMF reached GRUB from it (`BdsDxe: starting
Boot0001 "UEFI QEMU DVD-ROM …"`, `"… USB HARDDRIVE …"`); and SeaBIOS booted isolinux from it for
`ide`, `usb` and q35's `sata`. For a disk: the two-boot persistence run [below](#persistence) passed on
it. All of it on the boot-matrix image, which carries `serial-console`, `testkit`, `isohybrid` and
`uefi-bootable`.

**`ide` on `q35` is refused**, because q35 has no IDE controller: its `ide.N` buses are the ports of
the chipset's AHCI controller, so the flag would test SATA while saying IDE. Use `sata` there, or
`--machine pc` for the real thing. `ide` is also refused under `--uefi --kernel-boot`, where the
kernel cannot see it at all.

An ISO on `usb` needs an MBR, which is what [`isohybrid`](../50-cookbook/isohybrid.md) adds; without
one it is refused, since a stock image as a stick finds nothing to boot. It is attached
**read-only**: `-drive` opens read-write by default, and a boot that can alter the image under test is
not looking at it.

Boot order is set with `bootindex` on each device — the ISO first, then the disks in order — rather
than with `-boot`.

## Disks

`--disk FILE` attaches a disk and may be given more than once. A disk that does not exist is
created, and a disk that exists is never deleted or rewritten — the only file the launcher ever
removes is one it failed to finish creating, so a half-made disk cannot pass for a real one next
time.

| `--disk-create` | creates |
|---|---|
| `qcow2` (default) | a sparse qcow2 image, with `qemu-img` |
| `raw` | a sparse file of `--disk-size` |
| `ext4` | a raw file formatted ext4 and labelled `slaxperch`, ready for `perchdir=` |

`--disk-size` defaults to `8G`. An existing file is opened as whatever its header says it is — qcow2 or
raw, passed to QEMU explicitly rather than left for it to guess — and an existing block device such
as `/dev/sdb` can be attached the same way.

### Installing Slax to a virtual disk

```sh
tools/qemu/boot.py out/slax-custom.iso --bios --disk /var/tmp/slax-hd.qcow2 --disk-size 8G
```

On the default bus it appears in the guest as a plain IDE disk, `/dev/sda`, which is what Slax's
installer expects. Boot the ISO, then follow the `bootinst` procedure in
[write-to-usb](../40-workflow/write-to-usb.md) — partition the disk, format it (FAT32 works; that page
explains why ext4 is the better choice), copy `/slax/` onto it, and run `slax/boot/bootinst.sh` from
there. That page is the reference; this one only gets you a disk to do it to.

Two things about `bootinst.sh` that surprise people: it refuses to run as anything but root, and
when `$DISPLAY` is set it re-execs itself inside a terminal window rather than using the one you
started it from.

Then boot what you installed:

```sh
tools/qemu/boot.py out/slax-custom.iso --bios --disk /var/tmp/slax-hd.qcow2 --boot-disk
```

`--boot-disk` gives the first disk the first boot index and the ISO the second, so the ISO stays
attached as the fallback. Measured with a blank disk: SeaBIOS fails on it and goes on to boot the CD.

### Persistence

The CD menu offers no persistence, and the CD itself cannot hold a session — but `perchdir=` can name
a different, writable device, and livekit then keeps the session there
([bios-cd](../20-boot-sequence/bios-cd.md), and [persistence](../05-using-slax/persistence-perch.md)
for the long version). Installing to a disk, as above, gets you the USB/HDD menu's persistence
entries. Quicker is to give the ISO a disk and name it, either by pressing `Tab` at the menu and
adding the parameter, or with `--kernel-boot`, which skips the menu:

```sh
tools/qemu/boot.py out/slax-custom.iso --bios --kernel-boot \
    --disk /var/tmp/slax-perch.img --disk-create ext4 --disk-size 1G \
    --append perchdir=/dev/sda/slax/changes
```

Run it, change something, shut down; run the same command again. ext4 because livekit's storage
choice turns on a live POSIX test: a filesystem that passes gets a plain bind mount with no size
limit, while FAT gets a DynFileFS container — [Tier C](tier-c.md) has the details. This is the same
mechanism Tier C asserts on.

`perchdir=` names the disk as the guest sees it, and that follows the bus. Measured: two boots on one
ext4 disk each, with testkit's marker absent on the first boot and present on the second, on every
bus.

| `--disk-bus` | `perchdir=` |
|---|---|
| `ide`, `sata`, `scsi`, `usb` | `/dev/sda/slax/changes` |
| `virtio` | `/dev/vda/slax/changes` |
| `nvme` | `/dev/nvme0n1/slax/changes` |

All of it measured on Debian. **On both Slackware targets the second boot wedges**, on the default
IDE disk — [Tier C](tier-c.md) records it as issue #15 — and these runs did not try Slackware on the
other buses.

## Booting the kernel directly

`--kernel-boot` skips the bootloader. The launcher takes `/slax/boot/vmlinuz` and
`/slax/boot/initrfs.img` out of the ISO with `xorriso`, into its scratch directory, and has QEMU load
them — the same technique as `kitchen test --kernel`. The ISO is still attached, and livekit still
finds its data there; only isolinux and GRUB are left out. Its kernel command line is:

```
vga=normal rw printk.time=0 consoleblank=0 automount console=tty0 console=ttyS0,115200n8 from=/dev/sr0/slax
```

followed by anything in `--append`.

- **`console=tty0` comes before `console=ttyS0`.** The kernel prints to every console it is given, but
  `/dev/console` — where livekit's init writes — is the last one. This order puts init on the serial
  line and still shows the kernel on screen; [`serial-console`](../50-cookbook/serial-console.md)
  measured the reverse sending init to video.
- **`from=` is added only when the ISO really is `/dev/sr0`:** a CD on `ide`, `sata` or `scsi`. A stick
  is some `/dev/sdX`, so there livekit searches, as it would on real hardware — measured, it found
  `/dev/sda1`. A `from=` in `--append` replaces this one.
- **No boot index is set on anything.** QEMU gives the kernel loader index 0 itself and treats a second
  device claiming 0 as a hard error — `The bootindex 0 has already been used`, measured on `pc` and
  `q35` — so `--kernel-boot` refuses `--boot-disk`.
- **Under OVMF it cannot use IDE.** Measured on `pc`: after OVMF's direct kernel boot, `ata_piix`
  registers both IDE channels and finds nothing on either, so an IDE CD ends in `Could not locate slax
  data` and an IDE disk is simply absent. The same boot finds its data on `sata`, `scsi` and `usb`,
  SeaBIOS finds the IDE CD, and so does OVMF booting through GRUB — that is [Tier C](tier-c.md)'s
  UEFI path, on every target. So `--uefi --kernel-boot` defaults the ISO and disks to SATA, says so,
  and refuses an explicit `ide`. OVMF also adds `initrd=initrd` to the command line itself, which is
  harmless.

The ISO checks are skipped, since no firmware reads the ISO's boot records. `--append` without
`--kernel-boot` is refused: a boot through the menu takes its parameters from the ISO.

## Serial and monitor

| `--serial` | where the guest's serial line goes | the monitor |
|---|---|---|
| `stdio` (default) | this terminal, shared with the monitor | this terminal: `Ctrl-a c` |
| `file:PATH` | a log file | this terminal |
| `pty` | a pseudo-terminal qemu names when it starts | this terminal |
| `none` | nowhere | this terminal |
| `vc` | a qemu virtual console | this terminal, or curses' |

With `--display curses`, which owns the terminal, the serial line defaults to `vc`, and `stdio` is
refused. With `--display none` and `--serial file:…`, nothing appears anywhere but the log and the
monitor prompt, which is the useful shape for a boot you are watching from `tail -f`.

`--vga` picks the video card: `std` (the default), `virtio`, `cirrus`, `vmware` or `none`.

## Network

`--nic` picks the card, on QEMU's user-mode networking: `e1000` (the default), `e1000e`, `rtl8139`,
`pcnet` or `virtio`, or `none` for no network. The guest can reach out through the host; nothing can
reach in, except through `--ssh-port`:

```sh
tools/qemu/boot.py out/slax-custom.iso --bios --ssh-port 2222
ssh -p 2222 root@127.0.0.1
```

That forwards `127.0.0.1:2222` on the host to the guest's port 22 — useful on an image built with
[`enable-ssh`](../50-cookbook/enable-ssh.md), whose page explains what you can log in as.

## Printing instead of booting: `--print`

`--print` writes the commands for a boot to stdout and exits without running them. It is for the case
this repository has all the time: the image is built in a container with no `/dev/kvm`, and the KVM
host is where it should boot.

```console
$ tools/qemu/boot.py out/slax-boot-matrix-debian-64bit-12.2.0.iso --uefi --print 2>/dev/null
cp /usr/share/OVMF/OVMF_VARS_4M.fd out/slax-boot-matrix-debian-64bit-12.2.0.ovmf-vars.fd
qemu-system-x86_64 \
    -machine pc \
    -accel kvm -accel tcg \
    -m 2048 -smp 2 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=out/slax-boot-matrix-debian-64bit-12.2.0.ovmf-vars.fd \
    -device qemu-xhci,id=xhci \
    -drive if=none,id=iso,media=cdrom,readonly=on,format=raw,file=out/slax-boot-matrix-debian-64bit-12.2.0.iso \
    -device ide-cd,bus=ide.1,unit=0,drive=iso,bootindex=0 \
    -nic user,model=e1000 \
    -vga std -vnc 127.0.0.1:0 -device usb-tablet,bus=xhci.0 \
    -serial mon:stdio
```

What it guarantees:

- **stdout is shell and nothing else.** The summary and any notes go to stderr, and there are no
  comment lines, because an interactive zsh runs `#` as a command. Every word is quoted for `sh`.
- **It changes nothing here.** No firmware is copied, no kernel extracted, no disk created, and
  nothing about this machine is probed — not `/dev/kvm`, not the display backends, not the firmware
  directories. The ISO itself is still checked if it is here.
- **Firmware paths are the ones Debian 12 and Ubuntu 24.04 both ship** —
  `/usr/share/OVMF/OVMF_CODE_4M.fd` and `OVMF_VARS_4M.fd`, and for `--arch x86` the
  `OVMF32_*.secboot.fd` build with `q35` and SMM — because this machine's layout says nothing about
  the other one's. `--ovmf-code` and `--ovmf-vars`, or the `$OVMF_*` variables, print whatever you
  give them instead.
- **Scratch files go beside the ISO**, named after it: the variables copy as above, and a
  `.kernel-boot/` directory for `--kernel-boot`. Beside the ISO is normally `out/`, which git ignores.
- **Disks are created only where they are missing** (`[ -e FILE ] || …`), so pasting the same commands
  twice never formats the disk that holds the session you meant to resume.
- **Paths appear as you typed them** — escaped only where QEMU's option syntax needs it — so run the
  commands from the same relative place: the repository root, in the examples here.
- **QEMU decides the accelerator.** `-accel kvm -accel tcg` uses KVM where the host has it and falls
  back where it does not.

Verified end to end: a printed `--uefi --kernel-boot --disk-create ext4` script, run as printed,
booted to `Live Kit done` and wrote testkit's persistence marker; run again, it reused the disk and
found the marker there. The first attempt at that is also how the IDE problem above was found.

A typical round trip, with the ISO copied to the same `out/` in the host's clone:

```sh
tools/qemu/boot.py out/slax-custom.iso --uefi --print > out/run-uefi.sh   # in the container
sh out/run-uefi.sh                                                        # on the host
ssh -L 5900:127.0.0.1:5900 <host>                                         # from your workstation
```

## Memory and CPUs

The default is 2048 MiB, which matches the test harness and is comfortable for a desktop. `--mem`
takes MiB, or a number with an `M` or `G` suffix.

**"Run Slax from RAM" needs RAM at least the size of the ISO**, before the running system gets any.
That is fine for a 560 MiB image and a real constraint for a big one:

| ISO | `toram` wants |
|---|---|
| [`debian-browsers`](../50-cookbook/debian-browsers.md), 560 MiB | `--mem 1536` and up |
| [`all-browsers`](../50-cookbook/all-browsers.md), 1227 MiB | `--mem 3072` and up |

Without `toram` the image is read from the virtual CD as it goes, and 2048 is plenty for either.

`--smp` defaults to 2 CPUs, for a desktop — the test harness boots with one. `--cpu` passes a CPU
model through; `host` is refused where there is no KVM to back it, since QEMU's fallback to TCG
cannot run it (`max` works under both).

## What it needs

The packages are the ones the boot tests use:
[`containers/packages/boot.txt`](../../containers/packages/boot.txt) (`qemu-system-x86`, which
carries both `qemu-system-x86_64` and `qemu-system-i386`, `qemu-utils`, `ovmf`, `e2fsprogs`) and
`xorriso` from [`build.txt`](../../containers/packages/build.txt) for `--kernel-boot`, plus `python3`
3.9 or newer. `ovmf-ia32` is the one extra, and only for `--arch x86 --uefi`. A launch that is missing
something it needs says which package to install before it creates anything; `--print` needs none of
them, and instead lists what the machine that runs its commands will need.

## Options

| | |
|---|---|
| `--bios`, `--uefi` | firmware; one is required |
| `--ovmf-code FILE`, `--ovmf-vars FILE` | OVMF code image and variables template; default `$OVMF_CODE`/`$OVMF_VARS` (`$OVMF32_*` for x86), else searched — or, under `--print`, fixed paths |
| `--arch x64\|x86` | `qemu-system-x86_64` (default) or `qemu-system-i386` |
| `--machine pc\|q35` | default `pc`; SMM firmware selects `q35` |
| `-m`, `--mem SIZE` | guest RAM, default `2048` MiB |
| `--smp N` | CPUs, default `2` |
| `--cpu MODEL` | CPU model, default QEMU's own |
| `--accel auto\|kvm\|tcg` | default `auto`: KVM, falling back to TCG |
| `--iso-bus ide\|sata\|scsi\|usb` | how the ISO is attached; default the machine's own, or `sata` under `--uefi --kernel-boot` |
| `--kernel-boot` | load the ISO's kernel and initramfs directly |
| `--append PARAMS` | extra kernel parameters, with `--kernel-boot` |
| `--force` | boot an ISO the checks refuse |
| `--disk FILE` | attach a disk, creating it if absent; repeatable |
| `--disk-bus ide\|sata\|scsi\|virtio\|nvme\|usb` | bus for every disk; defaults by the same rule as `--iso-bus`, not to its value |
| `--disk-size SIZE` | size for a disk being created, default `8G` |
| `--disk-create qcow2\|raw\|ext4` | how a missing disk is created, default `qcow2` |
| `--boot-disk` | boot the first disk rather than the ISO |
| `--display vnc\|curses\|none\|gtk\|sdl` | default `vnc` |
| `--vnc-addr IP[:PORT]` | where the VNC server listens **on the machine running qemu**; default `127.0.0.1:5900`, or `boot-host.ini`'s `vnc`. An IP literal, bracketed if v6 |
| `--vnc-public` | confirm a `--vnc-addr` outside the private ranges; refused without it |
| `--local` | boot here even if `boot-host.ini` names a machine with KVM |
| `--vga std\|virtio\|cirrus\|vmware\|none` | video card, default `std` |
| `--serial stdio\|vc\|pty\|none\|file:PATH` | default `stdio`, shared with the monitor |
| `--tablet`, `--no-tablet` | USB tablet; default on for `vnc`, `gtk` and `sdl` |
| `--nic e1000\|e1000e\|rtl8139\|pcnet\|virtio\|none` | network card, default `e1000` |
| `--ssh-port PORT` | forward `127.0.0.1:PORT` to the guest's port 22 |
| `--print` | print the commands instead of running them |
| `-h`, `--help` | |

The exit status is QEMU's own — `0` when it was quit, or stopped with SIGTERM or SIGHUP, which it
handles — `2` for a refusal or a usage error, and `128+N` if QEMU was killed outright by signal `N`.

## How fast

KVM is used whenever the host has it, and the banner says which you got. The difference is large.
Measured on the build host with the 1227 MiB `all-browsers` ISO, direct kernel boot, bisecting the
harness budget: **all three livekit markers appear inside 5 seconds** with KVM. The same image under
TCG in an unaccelerated container wants the harness's 150-second budget. Add the menu's own timeout
to either figure when booting through it — 4 seconds for isolinux, 5 for GRUB by default.

So: with KVM this is an interactive tool. Without it, it is something you start and come back to.

## What it does not do

- **It asserts nothing.** Nothing here can fail a build or a commit. If you want an assertion, that
  is `kitchen test --kernel`, documented in [CI](ci.md). Nothing in CI runs the launcher either, so
  `tests/unit/test_tools_qemu.py` pins the commands it builds.
- **It does not replace real hardware.** QEMU tells you the image boots and the desktop works. It
  says nothing about the firmware, GPU or wireless chip in the machine you actually care about — and
  the GPU firmware question in particular is one stock Slax gets wrong.
- **Looking is not asserting.** `--iso-bus usb` and a persistence disk let you *look* at the USB route
  and at persistence; `ci/tier-c.sh` is what asserts on them. See [Tier C](tier-c.md).
