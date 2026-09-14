# `host-grub-entry` — boot Slax from a bootloader you already have

**Status: matrix-verified** — generated from the real menu entries and validated with `grub-script-check`.

```sh
kitchen apply host-grub-entry
# -> slax/boot/grub-snippet.cfg
```

## When to use it

The least invasive way to keep Slax on a machine that already boots something else. Copy `/slax/`
onto an existing partition and add a menu entry — **nothing is overwritten, no MBR is touched**.

Compare with `bootinst.sh`, which `dd`s `mbr.bin` over the first 440 bytes of the disk and sets the
active flag. That is right for a dedicated stick and wrong for a machine with an OS on it. See
[write-to-usb](../40-workflow/write-to-usb.md).

## What it produces

```grub
menuentry 'Resume previous session' {
    search --no-floppy --file --set=root /slax/boot/vmlinuz
    linux  /slax/boot/vmlinuz vga=normal load_ramdisk=1 prompt_ramdisk=0 rw printk.time=0 consoleblank=0 perchdir=resume automount
    initrd /slax/boot/initrfs.img
}
```

…one per entry in `syslinux.cfg`. Paste into `/etc/grub.d/40_custom` on the **host** — not the ISO —
then `update-grub` or `grub-mkconfig -o /boot/grub/grub.cfg`.

## Two details that make it portable

**`search --file --set=root`** locates whichever device holds `/slax/boot/vmlinuz` rather than
hardcoding `(hd0,1)`. The entry then survives repartitioning, a different USB port, or moving the
disk to another machine.

**`initrd=` is moved out of the kernel command line.** SYSLINUX takes it as a parameter; GRUB needs
a separate `initrd` command. Getting this wrong produces a kernel that boots and then panics with
`Unable to mount root fs` — the transformation is the main reason to generate this rather than copy
it by hand.

## Why generate it

Three pages of this documentation used to tell you to hand-write this block, and **the three
disagreed** — one had `--no-floppy`, the parameter order differed between them. Generating from the
real `syslinux.cfg` means the snippet cannot drift from what the ISO actually boots, and it picks up
entries other recipes added.

It is validated before being written:

```
grub-script-check: ok
```

`grub-script-check` is to GRUB what `sh -n` is to the initramfs scripts — it parses the file and
fails on a syntax error, so a malformed snippet is caught here rather than by someone whose boot
menu has silently lost an entry.

## Options

```yaml
- verb: boot.grub
  from: syslinux.cfg                   # or isolinux.cfg; default prefers syslinux
  probe: /slax/boot/vmlinuz            # the file `search` looks for
  dest: slax/boot/grub-snippet.cfg
```

Change `probe` if you renamed the directory, and `from` if you want the CD menu's entries (which
have no `perchdir=`, so no persistence) rather than the USB one's.

## Not the same as `boot.uefi`

| | |
|---|---|
| `boot.grub` | a snippet for a **host** bootloader to chainload this Slax |
| [`boot.uefi`](uefi-bootable.md) | builds GRUB **into the ISO's own** EFI System Partition, so the image boots on UEFI by itself |

They are independent and can both be applied.
