# 50 · Cookbook

One page per shipped recipe. Each states what it does, what it measured, and what it cannot do.

**Thirty-five recipes ship today.** Every one is built and structurally asserted on all four
targets by CI (or on the subset its `compat:` block declares) — with three exceptions:
[`all-browsers`](all-browsers.md), [`tor-browser`](tor-browser.md) and
[`firmware-refresh`](firmware-refresh.md) are built weekly rather than on every push, because four
sha256-pinned vendor keys, a version-pinned Tor Browser tarball and 65 files fetched from
linux-firmware mirrors are external dependencies — someone else's release should not redden
master. See
[`ci/slow-recipes.txt`](../../ci/slow-recipes.txt) and [CI](../60-testing/ci.md). Each page opens with the rung of
the [verification ladder](../../CONTRIBUTING.md) it actually reached — `matrix-verified`,
`artifact boot-verified`, `boot-verified` or `runtime-verified` — and the `95-status-vocab` gate
rejects any other word.

What is left is the work that needs more than YAML — a static busybox build, the `kernel.replace`
verb, and `boot-tools`, whose module source turned out not to be the obvious one.
[Project status](../00-overview/status.md) has the honest ledger.

```sh
kitchen apply <recipe>              # against an unpacked tree
kitchen build <profile>             # fetch -> unpack -> apply* -> pack -> test
```

## Shipped

### Boot and firmware

| | | priv |
|---|---|---|
| [`uefi-bootable`](uefi-bootable.md) | make the ISO boot on UEFI, which the stock ISO **cannot do at all** | ○ |
| [`isohybrid`](isohybrid.md) | add an MBR + GPT so the ISO can be `dd`'d to a stick | ○ |
| [`memtest86plus`](memtest86plus.md) | Memtest86+ 8.10 in the boot menu, BIOS and UEFI | ○ |
| [`serial-console`](serial-console.md) | log the whole boot to a serial port — the prerequisite for automated boot tests | ○ |
| [`boot-branding`](boot-branding.md) | replace the inaccurate help screen; give the menu time to be read | ○ |
| [`boot-cmdline`](boot-cmdline.md) | bake `toram` and friends into every entry; drop the broken `automount` | ○ |
| [`host-grub-entry`](host-grub-entry.md) | a GRUB entry for booting Slax from a bootloader you already have | ○ |
| [`iso-identity`](iso-identity.md) | label the image as yours, and write a verifiable checksum | ○ |
| [`firmware-refresh`](firmware-refresh.md) | firmware so the ISO works on more hardware — Debian's plus what only linux-firmware has; +53 MiB | ◐ |

The first two fix real gaps in every stock image, and together produce one file that boots four
ways: BIOS optical, UEFI optical, BIOS `dd`'d stick, UEFI `dd`'d stick.

### initramfs

| | | priv |
|---|---|---|
| [`initramfs-add-binary`](initramfs-add-binary.md) | put a static binary or script into early boot | ○ |
| [`initramfs-add-modules`](initramfs-add-modules.md) | promote drivers from a bundle so `find_data` can see your disk | ○ |
| [`initramfs-boot-timeout`](initramfs-boot-timeout.md) | change the 45 s `find_data` budget; the reference example for `initramfs.patch` | ○ |
| [`initramfs-busybox`](initramfs-busybox.md) | replace the 2017 busybox; 248 → 406 applets, only `catv` lost | ◐ |

Early boot runs before any bundle is mounted, so anything `find_data` or a `debug` shell needs has
to live here. The initramfs carries 301 modules against 4,766 in `01-core.sb`.

### Software

| | | priv |
|---|---|---|
| [`add-packages`](add-packages.md) | install distro packages into a new `07-*.sb`. The template recipe | ◐ |
| [`rootcopy-overlay`](rootcopy-overlay.md) | drop files straight onto the live filesystem — **no squashfs rebuild at all** | ○ |
| [`remove-bundle`](remove-bundle.md) | drop bundles by pattern — the only recipe that removes anything; the stock browser is −79 MiB Debian, −115 MiB Slackware | ○ |
| [`chromium-current`](chromium-current.md) | replace the 2023 browser — stock is chromium **117**, from September 2023 | ◐ |
| [`debian-browsers`](debian-browsers.md) | current Chromium **and** Firefox in one bundle — both arches, no vendor repos | ◐ |
| [`all-browsers`](all-browsers.md) | six browsers in one bundle — 64-bit; pair it with `remove-bundle` | ◐ |
| [`tor-browser`](tor-browser.md) | Tor Browser from a pinned tarball — 64-bit, runs as `guest`; pair it with `remove-bundle` | ◐ |
| [`firefox-esr`](firefox-esr.md) | add the second browser no Slax image ships; amd64 **and** i386 | ◐ |
| [`libreoffice`](libreoffice.md) | Writer, Calc, Impress and Draw — 116 MiB, the largest single addition here | ◐ |
| [`branding`](branding.md) | hostname, version string and login banner, from a 4 KiB override bundle | ○ |
| [`bundle-from-dir`](bundle-from-dir.md) | pack a directory of your own files as a filesystem root | ○ |
| [`bundle-from-tarball`](bundle-from-tarball.md) | fetch a published release tarball, verify it, pack it | ○ |
| [`bundle-from-txz`](bundle-from-txz.md) | pinned Slackware `.txz` via `installpkg` — the supported Slackware route | ◐ |
| [`renumber-bundles`](renumber-bundles.md) | change a bundle's place in the stack without rebuilding it | ○ |
| [`preinit-hook`](preinit-hook.md) | run your own code on the assembled filesystem, just before boot | ○ |

### System configuration

Everything here except `users-and-auth` is a plain file drop into a high-numbered bundle — sshd,
ConnMan and the desktop are all already installed, and only need switching or pointing somewhere.

| | | priv |
|---|---|---|
| [`enable-ssh`](enable-ssh.md) | switch on the sshd that both flavours already ship | ○ |
| [`users-and-auth`](users-and-auth.md) | replace the public `root`/`toor` password; add a user | ◐ |
| [`locale-timezone-keyboard`](locale-timezone-keyboard.md) | timezone and keyboard; and why `LANG` is more limited than it looks | ○ |
| [`network-preseed`](network-preseed.md) | ship a Wi-Fi network via ConnMan | ○ |
| [`kiosk-mode`](kiosk-mode.md) | boot straight into one fullscreen app, no desktop | ○ |
| [`fix-slackware-bugs`](fix-slackware-bugs.md) | five confirmed upstream defects, including completely broken TLS | ◐ |
| [`testkit`](testkit.md) | print facts about the assembled system so a boot test can assert them | ○ |

○ runs anywhere · ◐ needs a real `chroot` (`CAP_SYS_CHROOT` + `CAP_MKNOD`) —
see [container vs host](../40-workflow/container-vs-host.md)

## Which to reach for

Cheapest first. Prefer the first one that solves your problem:

| you want | use | cost |
|---|---|---|
| Slax to see a disk it currently cannot find | [`initramfs-add-modules`](initramfs-add-modules.md) | one repack, ~10 KiB |
| a config file, a script, an ssh key on the live system | [`rootcopy-overlay`](rootcopy-overlay.md) | **nothing** — copied in at boot |
| extra software | [`add-packages`](add-packages.md) | one `mksquashfs`, ~450 KiB |
| a smaller image | [`remove-bundle`](remove-bundle.md) | one bundle deleted |
| it to boot on a modern laptop | [`uefi-bootable`](uefi-bootable.md) | rebuild |
| it on a USB stick via `dd` | [`isohybrid`](isohybrid.md) + `uefi-bootable` | rebuild |

**Adding beats editing.** Bundle load order is the numeric prefix and higher wins, so a new
`07-mytools.sb` overrides `01-core.sb` without touching it — the shipped bundles stay byte-identical,
`kitchen probe` still reports `MATCH`, and the change is reversible by deleting one file. Editing a
shipped bundle is for **removal only**; nothing else needs it.
→ [bundles-squashfs](../10-anatomy/bundles-squashfs.md)

## Flavour coverage

**Twenty-four of the thirty-five work on all four targets.** Counted from the `compat:` blocks:
eight are Debian-only (`add-packages`, `all-browsers`, `chromium-current`, `debian-browsers`,
`firefox-esr`, `firmware-refresh`, `libreoffice`, `tor-browser`), two are Slackware-only
(`bundle-from-txz`, `fix-slackware-bugs`), and three ship 64-bit vendor builds only
(`all-browsers`, `bundle-from-tarball`, `tor-browser`). So a target's matrix runs 33 recipes on
debian-64bit, 30 on debian-32bit, 27 on slackware-64bit and 26 on slackware-32bit. The one that
most often surprises people:

| | |
|---|---|
| `add-packages` | **Debian only.** Slackware has no dependency-resolving package manager, and the shipped `slackpkg` configuration points at a dead host (`slackonly.com`, NXDOMAIN) and a mirror that has moved years past the frozen 2023 base. Installing from pinned `.txz` files does work and is the supported route — see the recipe page and [issues 8–9](../30-inventory/known-upstream-bugs.md). |

`ci/recipe-matrix.sh` applies every recipe **individually** to each of the four targets and asserts
on the result, which is what catches a recipe that silently only works on 64-bit Debian.

## Writing your own

A recipe is YAML validated against `schema/recipe.schema.json` before anything runs:

```yaml
apiVersion: slax-kitchen/v1
kind: Recipe
metadata:
  name: my-tools
  summary: Add the tools I always want
compat:
  flavours: [debian]
  arch: [32bit, 64bit]
  privilege: chroot
steps:
  - verb: bundle.packages
    bundle: 07-mytools
    packages: [tmux, ncdu, rsync]
```

- `compat.privilege` is what lets `kitchen doctor` tell you whether this machine can run it.
- `vars:` are substituted as `{{name}}`, and a profile can override them per build —
  see [recipes in a fork](../40-workflow/recipes-in-a-fork.md).
- `when:` guards a step on a fact about the tree, e.g. `flavour==debian`.
- **23 of 26 declared verbs are implemented.** A recipe using an unimplemented one fails with a
  message naming what *is* available. Full list and fields: [verb reference](../90-reference/verbs.md).

Every recipe needs a page here — `ci/checks/90-doc-coverage.sh` fails the commit otherwise, in both
directions.

## Not written yet

The larger gaps, roughly in the order they would be useful: `boot-tools`
(`hdt.c32`, `memdisk`, `chain.c32` — deferred, see
[bootloader-payloads](../10-anatomy/bootloader-payloads.md) for why the module source is not
obvious), `initramfs-busybox` (the shipped busybox is from 2017), and `kernel-replace`.
