# `libreoffice` — an office suite in the live session

**Status: matrix-verified** — built and structurally asserted on both Debian targets; every number
below is from a real build. Not booted to a desktop, so the applications have not been *run*.

```sh
kitchen apply libreoffice
```

Zero configuration. Debian only.

## Measured

LibreOffice **7.4.7-1+deb12u14**, built on both Debian targets:

| | 64-bit | 32-bit |
|---|---|---|
| bundle | **116 MiB** | **123 MiB** |
| packages | 110 | 110 |
| files | 7,065 | 7,152 |
| ISO | 416 → **532 MiB** | 416 → **539 MiB** |

Installed size of the 64-bit delta is 408 MB.

It compresses about 3.5:1 under xz, which is why the largest software addition in this cookbook
costs less on the ISO than the firmware refresh plus a browser.

## What you get, and what is deliberately left out

The default is **Writer, Calc and Impress**, not the `libreoffice` metapackage. **Draw arrives
anyway** as a dependency, so in practice it is four applications.

Two packages are named explicitly that you might not expect, because `bundle.packages` installs
with `--no-install-recommends` and both are Recommends rather than Depends:

- **`libreoffice-gtk3`.** Without it LibreOffice falls back to the `gen` VCL backend: it runs, but
  draws its own widgets, ignores the desktop theme and has no native file dialog. This is the
  difference between usable and merely present.
- **`hunspell-en-us`.** A word processor with no spell checker is a surprising thing to ship, and
  it is under a megabyte.

**Not included: `libreoffice-base`.** See below — this is not only about size.

## Why not the full metapackage?

Measured, the same way:

| set | bundle | packages |
|---|---|---|
| Writer + Calc + Impress + gtk3 (this recipe) | 116.2 MiB | 110 |
| `libreoffice` metapackage + gtk3 | 129.0 MiB | 121 |

**Only 13 MiB apart**, which is not the reason to prefer the smaller one. The reason is that the
metapackage gives you a **Base that cannot open a database**. Base needs a JRE, `default-jre` is a
Recommends, and with `--no-install-recommends` you get the application without the runtime it
requires. Shipping something that looks installed and fails when clicked is worse than not shipping
it.

The extra 11 packages are Base, Math, the `python3-uno` scripting bridge and Python 3.11.

## ⚠ You cannot add a JRE here

This is worth knowing before you try, because it is not a packaging choice — it is a limit of
building bundles without root's full capabilities:

```
$ kitchen apply <a recipe adding default-jre-headless>
error: package install failed (exit 100):
  -> This package's postinst runs a program that needs a real /proc. ...
the java command requires a mounted proc fs (/proc).
dpkg: error processing package openjdk-17-jre-headless:amd64 (--configure):
```

`_prepare_chroot` creates `/proc`, `/sys` and `/dev/pts` as **empty directories**. Mounting them
for real needs `CAP_SYS_ADMIN`, which a container generally does not have — check the `mount` row
in `kitchen doctor`. OpenJDK's postinst runs `java` to generate its class-data-sharing archive, and
`java` refuses to start without a mounted procfs.

So: **no JRE can be installed by `bundle.packages` on an unprivileged machine**, and therefore
neither can LibreOffice Base. On a host with `CAP_SYS_ADMIN` it should work; that has not been
tested here. `kitchen` recognises this failure and says so rather than leaving you to read a
hundred lines of dpkg output.

## Fonts worth adding

`fonts-opensymbol` comes in as a dependency; nothing else does. Documents authored in Microsoft
Office will substitute for Calibri and Cambria, which changes line breaks and pagination. If that
matters, copy this recipe and add:

```yaml
      - fonts-crosextra-carlito     # metric-compatible with Calibri
      - fonts-crosextra-caladea     # metric-compatible with Cambria
```

## Bundle number

`12-`, above the stock `01`–`06` and above the browsers at `10`–`11`, below the generated
`98-dpkg-db.sb` and a saved session at `99`. It carries no `var/lib/dpkg/status` — it ships a
fragment declaring its 110 packages, which `pack` merges:

```
98-dpkg-db.sb: 600 from 05-chromium.sb + 1 fragment(s) -> 702 packages
```

See [composing bundles](../40-workflow/composing-bundles.md).

## Slackware

Not supported. There is no official Slackware LibreOffice — it is an AlienBOB build — and
`slackpkg` points at a mirror years ahead of the frozen 2023 base, so packages installed from it
may not run. The supported route there is a pinned `.txz` through
[`bundle-from-txz`](bundle-from-txz.md).
