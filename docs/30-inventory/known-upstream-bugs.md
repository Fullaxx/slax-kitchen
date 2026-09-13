# Known upstream issues

Problems found in stock Slax 12.2.0 / 15.0.4 during analysis. None of these are slax-kitchen bugs;
they are recorded because they change what a customization toolkit has to do, and several are
invisible until they bite.

Upstream has had no release since 2023-10-10, so none of these are likely to be fixed upstream.

---

## 1. The ISO cannot boot on UEFI  · *both flavours, all four images*

The El Torito catalog has exactly one entry, platform `0x00` (x86 BIOS). There is no `0xEF` section
header and no EFI System Partition. `/slax/boot/EFI/Boot/bootx64.efi` exists but is a copy of
`syslinux.efi`, which can only read FAT — `bootinst.sh` relocates it to the root of a FAT USB stick.
On the ISO9660 filesystem it is unreachable.

**Fix:** the `uefi-bootable` recipe.

## 2. The ISO is not isohybrid  · *both flavours, all four images*

Bytes 0–511 are all zero: no MBR signature, no partition table, no GPT. `dd if=slax.iso of=/dev/sdX`
produces a stick that boots nothing. Upstream's only supported USB path is "copy `/slax/` onto FAT,
then run `bootinst`".

**Fix:** the `isohybrid` recipe.

## 3. `/etc/slax-version` says "64bit" on the 32-bit Debian ISO

`slax-32bit-debian-12.2.0.iso` ships `/etc/slax-version` reading `Slax 12.2.0 64bit` — byte-identical
to the 64-bit build's copy. The 32-bit Slackware build gets it right.

Anything keying off that file to detect bitness will be wrong. `kitchen probe` uses an ELF probe on
`usr/bin/ls` instead, and records both the claim and the truth. Note `bin/busybox` is useless for
this — it is i386 static on all four ISOs by design.

## 4. `genslaxiso` is broken on Slackware

It invokes `genisoimage`, which is not installed on the Slackware flavour — that build ships
`cdrtools`' `mkisofs` instead. Self-hosted ISO rebuilding therefore fails out of the box. The
argument sets are compatible, so `ln -s mkisofs /usr/bin/genisoimage` fixes it.

## 5. `pxe` calls tools the Slackware flavour does not have

`/usr/bin/pxe` calls `dhclient` (Slackware uses `dhcpcd`) and `busybox httpd` (there is no busybox in
the Slackware rootfs — only inside the initramfs).

## 6. `savechanges`' automount exclusion silently no-ops on Slackware

It greps `/etc/systemd/system/` for `Slax skip savechanges` markers. That path does not exist on the
Slackware flavour, which is sysvinit + elogind with no systemd at all, so the exclusion never fires.

## 7. `nosound` is documented but not implemented

`boot/help.txt` lists it. Nothing in `livekitlib`, the initramfs or either flavour's userland reads
it. Vestigial from Slax 9.x. Conversely `load=` **is** implemented but appears in no documentation.

## 8. `slackpkg` cannot update on a stock Slax 15.0.4 (as of 2026)

`/etc/slackpkg/slackpkgplus.conf` ships:

```
REPOPLUS=( slackpkgplus slackonly )
MIRRORPLUS['slackonly']=https://slackonly.com/pub/packages/15.0-x86_64/
```

**`slackonly.com` is NXDOMAIN.** `slackpkg update` fails on an unreachable repo, so a stock Slackware
Slax can install nothing until the dead repo is removed. This is ordinary repo rot for a release
dormant since 2023, and it will get worse.

`bundle.packages` resolves every `MIRRORPLUS` host and prunes unreachable repos from `REPOPLUS`
before running slackpkg.

## 9. `slackpkg`'s -current confirmation defeats `-batch=on`

The stock mirror is `slackware64-current`, while the installed base reports `15.0+`. slackpkg then
asks:

> You have selected a mirror for Slackware -current … but Slackware version 15.0+ appears to be
> installed. Is this really what you want?

`-batch=on -default_answer=y` does **not** cover this prompt. With no stdin it takes the default
(No), does nothing, and **still exits 0** — which looks exactly like success. This produced a
cheerful "built 07-extras.sb (4 KiB)" containing no packages at all before it was caught.

Two consequences for any tooling: feed `y` on stdin, and **never trust a package manager's exit code
— verify against the package database afterwards.** `bundle.packages` does both.

Separately, installing from `-current` into a 2023 base risks linking against a newer glibc than the
bundle ships. `bundle.packages` warns, and accepts a `mirror:` field to pin something stable.

## 10. `initramfs_create` derives applet symlinks by scraping usage text

```sh
$INITRAMFS/bin/busybox | grep , | grep -v Copyright | tr "," " " | ...
```

This parses busybox's human-readable help output, which is not a stable interface. `busybox --list`
has existed since well before the shipped 1.26.2 and is the documented way. Relevant to any
`initramfs-busybox` work.

## 11. The shipped busybox is from 2017

`BusyBox v1.26.2 (2017-12-14)`, identical on all four ISOs. Notable reachable issues include
CVE-2017-16544 (terminal-escape RCE via `ash` tab completion — and `/init` calls `debug_shell` six
times) and CVE-2022-48174 (`ash` stack overflow). It also predates `CONFIG_TIME64`, so `date -r` on
a post-2038 mtime already misbehaves.

## 12. `noautomount` is honoured by one component and ignored by the other

`fstab_create` in the initramfs tests `grep -vq automount /proc/cmdline`. The string `noautomount`
*contains* `automount`, so the test fails to short-circuit and automount proceeds:

```sh
$ echo 'vga=normal rw noautomount' > /tmp/c
$ grep -vq automount /tmp/c && echo skip || echo "proceed  <-- noautomount ignored"
proceed  <-- noautomount ignored
```

`slax-automount`, the udev-driven half, checks `grep -q noautomount` first and exits correctly. The
net effect of booting with `noautomount` is that `/media/*` entries are still written into
`/etc/fstab` at boot, but no new hotplug mounts appear afterwards.

**Workaround:** omit `automount` rather than negating it. The stock menu entries include it, so it
has to be removed from the `APPEND` line — `boot.cmdline` can do that.

The same substring-matching pattern applies to `debug`, `perch` and `toram`; only `text` is
word-anchored (`grep -q -w`). `perch` is the one case where the loose match is intentional, since
`perchdir=` and `perchsize=` are meant to imply it.
