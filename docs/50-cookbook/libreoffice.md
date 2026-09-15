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

## The two choices, and why

Neither is the one you would guess, so both are worth reading before you copy this recipe.

### 1. Three applications, not the `libreoffice` metapackage — and not for size

Measured, the same way as everything else here:

| set | bundle | packages |
|---|---|---|
| Writer + Calc + Impress + gtk3 (this recipe) | 116.2 MiB | 110 |
| `libreoffice` metapackage + gtk3 | 129.0 MiB | 121 |

**Thirteen megabytes.** That is not worth a decision, and if size were the reason this recipe
would ship the metapackage.

The reason is that the metapackage gives you a **Base that cannot open a database.** Base needs a
JRE; `default-jre` is a **Recommends, not a Depends**; and `bundle.packages` installs with
`--no-install-recommends`. So apt installs the application, leaves out the runtime it requires, and
does not complain. Measured: those 121 packages contain **no JRE at all**.

Shipping something that looks installed and fails when clicked is worse than not shipping it.

The extra 11 packages are Base, Math, the `python3-uno` scripting bridge and Python 3.11. **Draw
arrives as a dependency either way**, so this recipe is really four applications, not three.

If you want Base, read the next section first — you cannot simply add the JRE.

### 2. Two Recommends are named explicitly

`--no-install-recommends` is the right default for a bundle: it is what stops one package dragging
in a desktop's worth of suggestions. The cost is that it also drops things a package genuinely
needs to be *usable*, and nothing warns you. Two are worth paying for here:

- **`libreoffice-gtk3`.** Without it LibreOffice falls back to the `gen` VCL backend: it runs, but
  draws its own widgets, ignores the desktop theme and has no native file dialog. The difference
  between usable and merely present.
- **`hunspell-en-us`.** A word processor with no spell checker is a strange thing to ship, and it
  is under a megabyte.

The same reasoning applies to Carlito and Caladea — see [Fonts](#fonts-worth-adding) — which are
left out only because they matter solely for documents authored in Microsoft Office.

**This generalises.** Any recipe using `bundle.packages` should check the Recommends of what it
installs and decide deliberately, rather than discovering at boot that an application starts but
behaves oddly.

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
