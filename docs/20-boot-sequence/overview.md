# Boot, end to end

Five routes into the same kernel. They differ only in who loads `vmlinuz`; everything after that is
identical on every medium and both flavours.

```
              ┌─ BIOS + optical ──→ El Torito ──→ isolinux.bin ─┐
              │                                                  │
              ├─ BIOS + USB/HDD ──→ MBR → VBR → ldlinux.sys ────┤
 firmware ────┤                                                  ├──→ vmlinuz + initrfs.img
              ├─ UEFI + USB/HDD ──→ ESP → BOOTX64.EFI ──────────┤
              │                                                  │
              ├─ BIOS + PXE ──────→ pxelinux.0 ─────────────────┤
              │                                                  │
              └─ UEFI + optical ──→ ✗ nothing (see uefi-cd-gap) ─┘
                                                                  │
                     ┌────────────────────────────────────────────┘
                     ↓
            kernel unpacks initrfs.img into rootfs, executes /init
                     ↓
            transfer_initramfs ──→ tmpfs, switch_root
                     ↓
            drivers: loop, squashfs, fuse, aufs (or overlay), zram
                     ↓
            find_data ──→ mount the medium, locate slax/modules/*.sb     ← up to 45 s
                     ↓
            persistent_changes ──→ perch session, if requested
                     ↓
            copy_to_ram ──→ only with toram; unmounts the medium
                     ↓
            mount_bundles ──→ one loop mount per .sb
                     ↓
            init_union + union_append_bundles ──→ aufs, higher number wins
                     ↓
            copy_rootcopy_content ──→ cp -a data/rootcopy/* union/
                     ↓
            fstab_create ──→ /etc/fstab, plus /media/* if automount
                     ↓
            user_preinit ──→ sources data/rootcopy/run/preinit.sh
                     ↓
            change_root ──→ pivot_root, exec chroot . init
                     ↓
         ┌───────────┴────────────┐
         ↓                        ↓
    systemd (Debian)        sysvinit (Slackware)
         ↓                        ↓
    xorg.service            rc.M → Slax.03.startup
    ConditionKernelCommandLine=!text   grep -q -w text
         ↓                        ↓
         └──→ Xdetect -- :0 vt7 ──┘
                     ↓
            startx → /root/.xinitrc → startfluxbox
```

## The three handoffs that can fail

**Firmware → loader.** The most common failure, and it is always a mismatch between the medium and
how the image was written. A stock ISO `dd`'d to a stick has no MBR to hand to; a stock ISO in a UEFI
machine has no EFI boot entry. Both are properties of the image, not the machine — see
[`uefi-cd-gap.md`](uefi-cd-gap.md) and [`bios-usb-hdd.md`](bios-usb-hdd.md).

**Loader → kernel.** Rare. The loader has already read its configuration by this point, so a failure
here means a corrupt or truncated `vmlinuz`/`initrfs.img`, or a COM32 module from the wrong SYSLINUX
generation.

**`/init` → root filesystem.** `find_data` searching for 45 seconds and then `Could not locate slax
data` is the visible form. Usually a missing storage driver in the initramfs or a wrong `from=`.

## Where the flavours diverge

Only at the very end, and only in how X is started:

| | Debian 12.2.0 | Slackware 15.0.4 |
|---|---|---|
| PID 1 | systemd 252 | sysvinit + elogind, **no systemd at all** |
| X trigger | `xorg.service`, `ConditionKernelCommandLine=!text` | `/etc/rc.d/rc3.d/Slax.03.startup`, `grep -q -w text` |
| launcher | `/bin/su --login -c "/usr/bin/Xdetect -- :0 vt7 -ac -nolisten tcp"` | identical |
| networking | ConnMan | `rc.inet1` + dhcpcd |
| shutdown hook | systemd finds `/run/initramfs/shutdown` by convention | hand-written block in `rc.6` |

The `Xdetect` command line is byte-identical in both, which is why desktop-level customization is
flavour-agnostic even though the init systems are not.

## Timings

Rough, on a 2 GHz machine from a USB 3 stick:

| | |
|---|---|
| firmware → menu | 1–3 s |
| menu timeout | 4 s (`TIMEOUT 40`, tenths of a second) |
| kernel + initramfs load | 2–5 s — 21 MB to read |
| `modprobe_everything` | 1–3 s — 138 modules, network drivers skipped |
| `find_data` | **0–45 s** — the variable one |
| `mount_bundles` + union | < 1 s — loop mounts are lazy |
| init → desktop | 5–15 s |

`find_data` dominates when it goes wrong, because it retries for a full 45 seconds before failing.
Everything else is bounded.

## Observing it

Kernel messages are suppressed — `init_proc_sysfs` sets `printk` to 0 — so the green `*` lines from
`livekitlib` are all you normally see. To see more:

```
debug            drops to a shell at six points in /init, and again during shutdown
console=ttyS0    sends the whole thing to a serial port (the serial-console recipe)
```

Both are the supported ways in. There is no verbose mode short of `debug`.
