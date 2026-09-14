# slax-kitchen

A swiss-army knife for customizing [Slax](https://www.slax.org) ISOs. Pick a recipe, run one
command, get a bootable ISO back.

```sh
kitchen build example      # -> out/slax-example-12.2.0.iso
```

---

## Credit

**Slax and Linux Live Kit are the work of [Tomáš Matějíček](https://github.com/Tomas-M).** This
project customizes *his*; without it there is nothing here. If you find slax-kitchen useful, support
Slax upstream — there is a donate link on slax.org.

- **<https://www.slax.org>** — the project: downloads, changelog, blog, documentation, donate
- **<https://www.linux-live.org>** — Linux Live Kit, the framework Slax is built on
- **<https://github.com/Tomas-M/linux-live>** — the actual source, containing both Linux Live Kit
  *and* the complete official Slax build system under `Slax/debian12/` and `Slax/slackware15/`.
  There is no separate `Tomas-M/slax` repository; this is it.
- **<https://github.com/Tomas-M>** — components Slax depends on directly: `dynfilefs` (persistence
  containers), `httpfs2-enhanced` (`from=http://` booting), `ncurses-menu` (the session and device
  pickers), `xlunch` (the app launcher)
- **<https://ftp.linux.cz/pub/linux/slax/>** — the official release mirror

Slax and Linux Live Kit are **GPLv2**. This toolkit is MIT. Upstream is vendored unmodified under
`vendor/` and never relicensed — see [NOTICE.md](NOTICE.md).

---

## Quick start

```sh
git clone --recurse-submodules https://github.com/Fullaxx/slax-kitchen
cd slax-kitchen
./kitchen doctor                      # what this machine can and cannot do
./kitchen fetch debian-64bit-12.2.0   # download + verify a base ISO (~416 MiB)
./kitchen build example               # -> out/slax-example-12.2.0.iso
```

`kitchen doctor` tells you which tools are missing and what provides them. Nothing destructive runs
without checking its requirements first.

`kitchen fetch` downloads from a mirror list and checks **both** size and sha256 against
`compat/sources.yaml` before accepting the file, falling through to the next mirror if either
fails — so a stale or hostile mirror cannot poison a build. An ISO you already have works too:
`kitchen build --base /path/to.iso`.

### Two things the stock ISO cannot do

Both were confirmed by parsing the official images byte for byte, and both are fixed by recipes here:

| | Why | Recipe |
|---|---|---|
| **Boot on UEFI** | its El Torito catalog has exactly one entry, for x86 BIOS. There is no EFI entry and no EFI System Partition | `uefi-bootable` |
| **Be `dd`'d to a USB stick** | bytes 0–511 are all zero: no MBR, no partition table, no GPT | `isohybrid` |

Upstream's supported USB path is "copy `/slax/` onto a FAT stick and run `bootinst`", which still
works and is documented in [install-to-usb](docs/05-using-slax/install-to-usb.md).

---

## What Slax is

A live operating system that runs from CD, USB or disk without installing anything. Two flavours,
same boot chain:

| | Base | Init | Size |
|---|---|---|---|
| **Slax 12.2.0** | Debian 12 (bookworm) | systemd | 416 MiB |
| **Slax 15.0.4** | Slackware 15.0+ | sysvinit + elogind | 455 MiB |

The root filesystem is a **union of numbered squashfs bundles** — `01-core`, `02-xorg`,
`03-desktop` and so on — stacked with aufs by an initramfs built from Linux Live Kit. Nothing is
installed; changes live in RAM unless you ask for persistence.

**Load order is the numeric prefix, and higher wins.** That single rule explains most of how Slax is
customized: drop a `07-mytools.sb` in and it overrides everything below it.

---

## Using Slax

Enough to be productive; the long form is in [docs/05-using-slax/](docs/05-using-slax/).

**Log in** as `root` / `toor` — the banner on tty1 says so. A `guest` account exists because
Chromium refuses to run as root.

**The desktop** is Fluxbox with an xfce4-panel and the xlunch launcher.

| Key | Does |
|---|---|
| `Super` or `Alt+F2` | app launcher |
| `Alt+F1` | terminal |
| `Print` | screenshot |
| right-click desktop | menu |

**Boot parameters** worth knowing — press `Esc` at the boot menu, then `Tab` to edit:

| | |
|---|---|
| `toram` | copy everything to RAM, then eject the medium |
| `text` | skip the desktop, console only |
| `perchdir=resume\|new\|ask` | persistent changes (USB/HDD only) |
| `noload=05-chromium` | skip a bundle this boot |
| `from=/dev/sdb1/slax` | look for Slax data somewhere specific |
| `debug` | drop to a shell at six points during init |

**Persistence** is off on CD and on by default from a USB stick. On a Linux filesystem it is
unlimited; on FAT32 or NTFS it lives in a growable 16 GB container file. See
[persistence-perch](docs/05-using-slax/persistence-perch.md) — it is the feature people most often
get wrong.

**Bundles at runtime**: `slax activate foo.sb` loads one without rebooting, `slax list` shows what
is loaded, `savechanges` freezes your current session into a permanent `99-changes-N.sb`.

---

## Installing to media

| Medium | How |
|---|---|
| **CD/DVD** | burn the ISO. Works as shipped, but **no persistence** — optical media cannot be written to. |
| **USB / HDD** | copy `/slax/` onto a FAT32 partition and run `slax/boot/bootinst.sh` (Linux) or `bootinst.bat` (Windows). This is upstream's supported route and gives you persistence and UEFI. |
| **USB by `dd`** | only after applying the `isohybrid` recipe. A stock ISO written this way boots nothing. |

Details and the gotchas in [install-to-usb](docs/05-using-slax/install-to-usb.md) and
[install-to-harddisk](docs/05-using-slax/install-to-harddisk.md).

---

## Customizing

A recipe is a YAML file naming one or more operations:

```yaml
apiVersion: slax-kitchen/v1
kind: Recipe
metadata:
  name: my-tools
  summary: Add the tools I always want
compat:
  flavours: [debian]
  privilege: chroot
steps:
  - verb: bundle.packages
    bundle: 07-mytools
    packages: [tmux, ncdu, rsync]
```

```sh
./kitchen unpack isos/slax-64bit-debian-12.2.0.iso
./kitchen apply my-tools
./kitchen pack                     # -> out/slax-...-custom.iso
```

Fourteen recipes ship today, all verified by booting the result:

| | |
|---|---|
| `uefi-bootable` | make the ISO boot on UEFI firmware |
| `isohybrid` | make it `dd`-able to a USB stick |
| `memtest86plus` | add Memtest86+ 8.10 to the boot menu |
| `add-packages` | install distro packages into a new bundle |
| `remove-chromium` | drop the browser bundle (−79 to −115 MiB) |
| `remove-bundle` | drop any bundle by regex |
| `rootcopy-overlay` | drop files onto the live system with no rebuild |
| `serial-console` | add a serial boot entry, for headless and CI |
| `initramfs-add-binary` | put a static tool into early boot |
| `initramfs-add-modules` | promote drivers into the initramfs so `find_data` sees your disk |
| `initramfs-boot-timeout` | change how long early boot waits for the Slax data |
| `branding` | hostname, version and login banner via an override bundle |
| `boot-branding` | an accurate boot help screen, and a readable menu timeout |
| `host-grub-entry` | boot Slax from a GRUB you already have, nothing overwritten |

See [the cookbook](docs/50-cookbook/), [all 20 verbs](docs/90-reference/verbs.md), and
[the CLI reference](docs/90-reference/cli.md).

---

## Compatibility

Four supported targets, each fingerprinted in `compat/`:

```
debian-64bit-12.2.0     debian-32bit-12.2.0
slackware-64bit-15.0.4  slackware-32bit-15.0.4
```

These are the newest upstream releases — Slax has shipped nothing since **2023-10-10**.
`kitchen probe <iso>` identifies any ISO and reports what differs from stock, attributing changes to
the recipes that caused them. A weekly CI job watches for a new release.

---

## Status

See **[docs/00-overview/status.md](docs/00-overview/status.md)** for what is verified, what is
written but unsupported, and what does not exist yet. Short version: the core loop
(unpack → apply → pack → test) and fourteen recipes are boot-verified, and the documentation of Slax
itself is complete; roughly two thirds of the planned recipes are not written yet.

## Licensing

MIT for this repository's own code, docs and recipes — see [LICENSE](LICENSE). Slax, Linux Live Kit
and everything inside a built ISO carry their own licences; [NOTICE.md](NOTICE.md) sets out the
boundary and the obligations that come with redistributing an image.
