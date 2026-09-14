# `locale-timezone-keyboard` — the three things everyone changes first

**Status: artifact boot-verified.** Built and structurally asserted on all four
targets; then booted under QEMU with [`testkit`](testkit.md), which confirmed `/etc/timezone` and the 2301-byte tzfile reached the union.
**Runtime behaviour is still untested** — that the file is correct is not the same as
the feature working, and the difference needs a full desktop boot.

```sh
kitchen apply locale-timezone-keyboard
```

```yaml
vars:
  timezone: Europe/Prague
  xkblayout: us
  locale: en_US.UTF-8        # read the warning below before changing this
```

Stock Slax boots as `Etc/UTC` with a US keyboard. Two of those three are easy to fix; the third is
not, and the difference is worth understanding before you rely on it.

## What works, and where

| | Debian | Slackware |
|---|---|---|
| **timezone** | ✅ | ✅ |
| **X keyboard** | ✅ | ✅ |
| **console keyboard** | ✅ | ❌ **not possible** |
| **`LANG`** | ⚠ only the built locales | ⚠ only the built locales |

## Timezone

`/etc/localtime` is a **symlink** on Debian and a **real file** on Slackware — which is what
Slackware's own `timeconfig` produces. `bundle.files` writes regular files only, so the recipe
copies the tzfile. That is correct on Slackware by convention and accepted on Debian, where a real
`/etc/localtime` is the older but still supported form.

The tzfile comes from the **build host's** `/usr/share/zoneinfo`, not the image's. The image does
carry zoneinfo (900 files on Debian, 1866 on Slackware), but a recipe cannot reference a path
inside the tree it is building. It is 2.3 KiB, and the tzfile format has been stable for decades.

Each flavour also gets its companion file: `/etc/timezone` on Debian, `/etc/hardwareclock` on
Slackware. The latter is left saying `localtime`, meaning the RTC holds local time rather than UTC —
which is what Windows does, and what you want on a dual-boot machine.

## Keyboard

Two hooks, both shipped:

- **`/root/.fluxbox/kblayout`** — `.fluxbox/startup` runs `fbsetkb $(cat ~/.fluxbox/kblayout)`,
  which is really `setxkbmap $1`. This drives the live desktop session. The stock value is `en`,
  which **is not an XKB layout at all**, so X silently falls back to `us`.
- **`/etc/X11/xorg.conf.d/00-keyboard.conf`** — covers X started any other way.

**Never write `01-xautodetected.conf`.** `/usr/bin/Xdetect` deletes and regenerates that exact
filename on every boot, so a file placed there vanishes. `00-` sorts earlier and is never touched.

### Why there is no Slackware console keymap

`rc.M` runs `/etc/rc.d/rc.keymap` if it is executable, and that file does not ship. Worse: **the
`kbd` package is not installed on any Slackware bundle.** No `loadkeys`, no `setfont`, no
`dumpkeys`, no `/usr/share/kbd/keymaps`. A recipe that dropped an executable `rc.keymap` calling
`loadkeys` would run and fail silently — the worst kind of not-working.

Fixing it means shipping the binaries and keymap data, which is a larger recipe than this one. On
Debian, `console-setup` recomputes from `/etc/default/keyboard` on every boot (the image ships no
cached keymaps), so dropping that file is enough.

## `LANG` — read this before changing it

**Only `en_US.UTF-8`, `C`, `C.UTF-8` and `POSIX` exist on the image.** Setting anything else
produces a *broken* locale, not a translated system.

The reason is that the locale **source data** has been stripped:

| | |
|---|---|
| Debian | `/usr/share/i18n/locales/` holds 5 files — `C`, `POSIX`, `eo`, `i18n`, `syr`. `locale-archive` contains only `en_US.utf8`. |
| Slackware | `/usr/share/i18n` is an **empty directory**, present only in `06-devel`. Two compiled locales ship: `en_US` and `en_US.utf8`. |

So `locale-gen` and `localedef` cannot build `de_DE.UTF-8` **even in a chroot** — there is nothing
to build it from. To actually get another locale you must ship a prebuilt `/usr/lib/locale/<name>/`
tree, compiled on a host with matching glibc, in your own bundle.

The file differs per flavour: `/etc/default/locale` on Debian, `/etc/profile.d/lang.sh` on
Slackware. The Slackware replacement keeps upstream's `LC_COLLATE=C`, because changing collation
changes the result of every `sort` and every shell glob range.

## One step per flavour

`bundle.files` builds a bundle in one shot and refuses to write one that already exists, so several
steps cannot accumulate into the same bundle. The recipe is therefore split **by flavour**, not by
concern — each step carries that flavour's complete set, and exactly one runs. It costs a little
duplication of the three shared files and makes the whole per-flavour set visible in one place.
