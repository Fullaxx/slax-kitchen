# `kiosk-mode` — boot into one fullscreen application

**Status: artifact boot-verified.** Built and structurally asserted on all four
targets; then booted under QEMU with [`testkit`](testkit.md), which confirmed the 1020-byte `.xinitrc` reached the union, beating 03-desktop's 13-byte copy.
**Runtime behaviour is still untested** — that the file is correct is not the same as
the feature working, and the difference needs a full desktop boot.

```sh
kitchen apply kiosk-mode
```

```yaml
vars:
  command: "xterm -maximized -e '…'"
```

## `/root/.xinitrc` is the hook

```
xorg.service (Debian) / rc3.d/Slax.03.startup (Slackware)
  └─ Xdetect          regenerates 01-xautodetected.conf, then calls startx
      └─ startx       reads /root/.xinitrc          <-- cut here
          └─ startfluxbox → /root/.fluxbox/startup
              └─ fluxbox, xfce4-panel, compton, volumeicon, xlunch, …
```

Replacing `.xinitrc` cuts the chain at its narrowest point — **13 bytes on Debian**
(`startfluxbox`), 21 on Slackware. Everything below it never starts: no window manager, no panel,
no compositor, no tray, no launcher. That is what a kiosk wants, and it is far cleaner than
starting the desktop and then hiding it.

**Do not replace `/root/.fluxbox/startup` instead.** The two flavours' copies differ by seven lines
(Slackware's starts `connmand` and sets the volume with `pamixer`), so a single replacement would
silently regress one of them. `.xinitrc` is effectively identical on both, which is why this recipe
needs no `when:` guard at all.

On Slackware `.xinitrc` is shipped **twice** — `02-xorg` has a blackbox version, `03-desktop`
overrides it with the fluxbox one. A bundle numbered above both wins, so `08` is correct and `02`
would not be.

## What the replacement has to do

| | |
|---|---|
| `xset s off` / `-dpms` | normally done by `.fluxbox/startup`, which no longer runs. A kiosk that blanks after ten minutes is a broken kiosk. |
| `exec` the app | so it becomes the session leader: when it exits, X exits, which is what the service expects. |
| never exit quickly | if X dies within ten seconds, `Xdetect` treats it as a failed autodetect, deletes its config and retries **once**. A command that exits immediately gives a black screen, not a retry loop. |

There is no window manager, so the application owns its own geometry. Most kiosk-capable apps have
a flag — `chromium --kiosk`, `firefox --kiosk`, `mpv --fullscreen`. If yours needs a WM to go
fullscreen, exec a tiny one and background the app instead.

## Chromium will not run as root

That is precisely why the `guest` account exists. To kiosk Chromium:

```yaml
command: "su guest -c 'chromium --kiosk --no-first-run https://example.org'"
```

Be aware that `.fluxbox/startup` is what normally bind-mounts `/home/guest/Desktop` and friends over
`/root/`. With the desktop bypassed those bind mounts do not happen — usually fine for a kiosk, and
occasionally surprising.

## Getting back out

A kiosk image with no shell is easy to lock yourself out of. Two ways back in, both without
rebuilding:

- boot with **`text`** on the kernel command line — both flavours check it before starting X at all
  (`ConditionKernelCommandLine=!text` on Debian, a `grep -q -w text` on Slackware), so you get a
  console.
- boot with **`noload=08-kiosk`** to skip the bundle for one boot.
