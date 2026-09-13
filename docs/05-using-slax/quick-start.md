# Quick start

## Boot it

Burn the ISO to a CD, or see [install-to-usb](install-to-usb.md). It boots in about 15 seconds on
real hardware.

The boot menu is **hidden by default** — press **Esc** during the four-second window to see it, or
just wait and it boots the first entry. From a CD that is *Run Slax from CD*; from a USB stick it is
*Resume previous session*.

## Log in

The console banner tells you:

```
                       Your login name: root
                           Password: toram
```

— except it says `toor`, which is "root" backwards. There is also a `guest` account (uid 1000) with
the same password, which exists because Chromium refuses to run as root; Slax launches the browser
as `guest` for you.

You normally will not see a login prompt at all: Slax starts X automatically on vt7 as root and
lands you on a desktop. Press `Ctrl+Alt+F1` for the console.

## The desktop

Fluxbox, with an **xfce4-panel** along the bottom and **xlunch** as the fullscreen app launcher.

| Key | Does |
|---|---|
| `Super` (Windows key) or `Alt+F2` | fullscreen app launcher |
| `Alt+F1` | terminal |
| `Print` | screenshot |
| right-click on the desktop | the Fluxbox menu |
| the leaf icon, bottom left | the same launcher |

## First five minutes

**Network** should already be up via DHCP. If not, the two-computers icon in the system tray opens
ConnMan, which handles Wi-Fi too.

**Install something**: `apt install mc` works normally on the Debian flavour — Slax wraps `apt` so
it runs `apt update` for you the first time. It installs into RAM and is gone at reboot unless you
save it. See [bundles-at-runtime](bundles-at-runtime.md).

**Keep your changes**: from a USB stick they are saved automatically. From a CD they cannot be —
see [persistence-perch](persistence-perch.md).

**Change the screen resolution**: right-click the desktop → *Screen resolution*, or `fbscreensize
1600x900` in a terminal.

**Change the keyboard layout**: `fbsetkb de`, or pick the flag in the panel.

## Shutting down

Use the launcher's logout icon, or `poweroff` in a terminal. Slax unmounts everything cleanly and
**ejects the CD tray**, then closes it again after six seconds — that is deliberate, so you can grab
the disc.
