# Troubleshooting

## It does not boot at all

**On a UEFI machine, from a CD or a `dd`'d stick.** Expected: the stock ISO is **BIOS-only**. Its
El Torito catalog has one entry, for x86 BIOS, and there is no EFI System Partition. Either enable
CSM/Legacy in firmware, use the `bootinst` route in [install-to-usb](install-to-usb.md), or build an
ISO with the [`uefi-bootable`](../50-cookbook/uefi-bootable.md) recipe.

**From a `dd`'d stick, on any firmware.** Also expected: a stock ISO has no MBR, no partition table
and no GPT — bytes 0–511 are all zero. Apply [`isohybrid`](../50-cookbook/isohybrid.md), or use
`bootinst`.

**After running `bootinst.sh`.** Check it actually ran `extlinux --install` rather than failing on a
`noexec` mount — its output says. Check the partition is marked active and that you copied the whole
`slax/` directory, not just its contents.

## It starts, then says it cannot find the data

```
Could not locate slax data
```

The initramfs scanned every block device for 45 seconds and found no `/slax/*.sb`. Usually:

- the directory was renamed, or only partially copied;
- the storage controller needs a driver that is not in the initramfs (exotic RAID, some NVMe);
- USB enumeration is slower than 45 seconds on that machine.

Boot with `debug` to get a shell at that exact point, then look at `blkid` and `ls /memory/data`.
`from=/dev/sdXN/slax` skips the scan entirely.

## The desktop does not start

You land on a console instead. Either `text` is on the command line, or X failed.

Slax's `Xdetect` runs `Xorg -configure`, and **if X exits within ten seconds it retries once with the
generated config removed**. So a persistent failure is usually a genuine driver problem. Check
`/var/log/Xorg.0.log`. Boot with `text` deliberately if you only need a console.

Remember `01-firmware` contains **no GPU firmware**, so removing it never causes this.

## My changes disappeared

Almost always one of:

- **Booted from a CD.** Persistence is impossible there; the session menu entries are greyed out.
- **Booted the "Run Slax from RAM" / `toram` entry**, which unmounts the medium.
- **No `perchdir=` on the command line.** Check `cat /proc/cmdline`.
- **Persistence is on but the medium is read-only**, e.g. a `dd`'d ISO, which is ISO9660.

Verify quickly:

```sh
mount | grep memory/changes
```

If that is `tmpfs`, you are running in RAM. See [persistence-perch](persistence-perch.md).

## Persistence works but fills up immediately

You are on FAT32 or NTFS, where changes live in a fixed-size container that defaults to ~16 GB —
and the **size is set when it is first created**. `perchsize=` on a later boot does nothing unless
you increase it. Delete `changes.dat*` in the session directory to start over, or reformat the stick
ext4 and get unlimited persistence.

## `slax activate` fails

```
Unable to deactivate Slax Bundle - still in use
```

Something has files open in that bundle. Close it; `dmesg` names the holder.

If `activate` fails outright, check the union is aufs: `df /` should say `aufs`. These commands are
**aufs-only** and do not work under the overlayfs fallback.

## `slackpkg` does nothing, or reports success having installed nothing

Known, and not your fault. Two causes, both upstream:

- `slackpkg update` fails because `slackonly.com` — hardcoded in Slax 15.0.4's config — no longer
  resolves. Remove it from `REPOPLUS` in `/etc/slackpkg/slackpkgplus.conf`.
- slackpkg asks "you selected a `-current` mirror but 15.0+ is installed, is this really what you
  want?", takes the default No when nothing answers, and **still exits 0**.

See [add-packages](../50-cookbook/add-packages.md).

## `genslaxiso` says `genisoimage: command not found`

Slackware flavour only, and upstream's bug: the script calls `genisoimage` but that build ships
`mkisofs`. `ln -s /usr/bin/mkisofs /usr/bin/genisoimage` fixes it; the arguments are compatible.

## Getting more information

`debug` on the kernel command line drops you to a shell at six points during init — before hardware
probing, after finding the data, after persistence setup, after mounting bundles, after building the
union, and before `change_root`. It is the single most useful diagnostic Slax has.

The full list of known upstream issues is in
[known-upstream-bugs](../30-inventory/known-upstream-bugs.md).
