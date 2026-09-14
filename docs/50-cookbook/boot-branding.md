# `boot-branding` — an accurate help screen, and a menu you can read

**Status: runtime-verified** — applied and booted; the BIOS screenshot confirms the changed
default actually takes effect.

```sh
kitchen apply boot-branding
```

Both menu files are **byte-identical across all four targets**, so one edit applies everywhere.

## Why the shipped help is worth replacing

`F1` at the boot menu shows `/slax/boot/help.txt`. It is wrong in both directions:

| | |
|---|---|
| **documents `nosound`** | which has **no implementation anywhere** — not in the initramfs, not in `livekitlib`, not in any bundle on either flavour. Setting it does nothing at all |
| **omits `load=`, `cache=`, `ip=`, `text`** | all four are implemented |

The replacement is built from [the authoritative parameter list](../20-boot-sequence/boot-parameters.md),
which was produced by grepping every consumer rather than by trusting the existing text. It also
spells out the two asymmetries that catch people:

- `noload=` translates commas to "or"; **`load=` does not**, so `load=xorg,desktop` matches a literal
  string and loads nothing.
- persistence needs writable media — a CD cannot persist no matter what you pass.

## `TIMEOUT` is in tenths of a second

The stock value is `TIMEOUT 40`, which is **four seconds**, not forty. Long enough to miss if you
glance away.

```yaml
vars:
  timeout: "10"          # seconds; the verb converts
```

Taking seconds and converting is deliberate — passing tenths straight through is how you end up
with a one-second menu nobody can read.

## Changing the default entry

```yaml
- verb: boot.branding
  default: toram
```

The label must already exist, and the verb says so if it does not:

```
no LABEL 'nosuchlabel' in isolinux.cfg; have default, toram. Add it with boot.menu first.
```

**Prefer adding an entry to changing the default.** An added entry leaves a way back; a changed
default is the thing every subsequent boot does. If you do change it, verify by booting — the BIOS
screenshot test caught this working exactly because `toram` behaves visibly differently:

```
[ TIME ] Timed out waiting for device dev-sr0.device
[DEPEND] Dependency failed for media-sr0.mount
```

That is not an error. `toram` copies to RAM and unmounts the medium, so the `automount` entry for
`/dev/sr0` has nothing left to mount — which is also the proof the new default took effect.

## Splash images

```yaml
- verb: boot.branding
  bootlogo: ./mysplash.png      # the menu background
  helpbg:   ./myhelpbg.png      # behind the F1 help screen
```

The shipped `bootlogo.png` is 23,368 bytes and `zblack.png` 6,447. Keep to the same dimensions —
`vesamenu.c32` draws the menu at fixed offsets (`MENU VSHIFT 17`, `MENU ROWS 5`), so a differently
proportioned image moves the text off the artwork rather than rescaling.

## What this does not touch

The **menu layout** — colours, row positions, `MENU WIDTH`, the `MENU AUTOBOOT` string — is left
alone. Those are `boot.menu`/`boot.cmdline` territory or a hand edit, and changing them well needs
you to see the result; see [edit-bootloader](../40-workflow/edit-bootloader.md).
