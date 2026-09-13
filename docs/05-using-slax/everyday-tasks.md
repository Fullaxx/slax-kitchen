# Everyday tasks

## Networking

**Debian flavour** uses **ConnMan**. The two-computers icon in the system tray opens `connman-gtk`,
which handles Ethernet, Wi-Fi and VPN. Wired DHCP just works on boot.

**Slackware flavour** uses Slackware's own `rc.inet1` plus `dhcpcd`; configuration lives in
`/etc/rc.d/rc.inet1.conf`.

`/etc/resolv.conf` ships pointing at `8.8.8.8` and `8.8.4.4`. If your network needs its own
resolver, edit it — but note that persistence deliberately excludes `/etc/resolv.conf`, so the
change will not survive a reboot. Put it in `/slax/rootcopy/etc/resolv.conf` instead, which is
copied over the filesystem at every boot.

## Installing software

**Debian**: `apt` works normally.

```sh
apt install mc
```

Slax wraps `apt` and `apt-get` as shell functions in `/root/.bashrc` that run `apt update`
automatically the first time, so you do not get the usual "package not found" on a fresh boot.

**Slackware**: `slackpkg` is preconfigured, but see the warning in the
[add-packages cookbook page](../50-cookbook/add-packages.md) — the stock mirror points at
`slackware64-current`, which has moved years past the 2023 base, and one of the configured extra
repositories (`slackonly.com`) no longer resolves at all.

Either way, **installed software lives in RAM and is gone at reboot** unless you have persistence
on, or run `savechanges`. See [bundles-at-runtime](bundles-at-runtime.md).

## Keyboard layout

```sh
fbsetkb de
```

or click the flag in the panel. The setting is remembered in `~/.fluxbox/kblayout`.

## Screen resolution

```sh
fbscreensize 1600x900
```

or right-click the desktop → *Screen resolution*, which is generated from what `xrandr` reports.

Slax detects VirtualBox and VMware at startup and bumps the resolution automatically.

## Other disks

With `automount` on the kernel command line — which the stock menu sets — every other disk is
mounted under `/media/<device>` and added to `/etc/fstab` at boot. Hot-plugged disks are handled by
a udev rule that writes a systemd mount unit (Debian) or mounts directly (Slackware), and the file
manager's bookmarks are updated.

Turn it off with `noautomount`.

## Screenshots

Press `Print`. Images land in your home directory.

## The web browser

The launcher's **Web Browser** entry runs Chromium as the `guest` user, because Chromium refuses to
run as root. If the bundle has been removed, the same entry offers to install it — on Debian via
`apt`, on Slackware by downloading the bundle from slax.org.

## Becoming another user

The desktop runs as root. `su - guest` if you need the unprivileged account; the password is the
same `toor`.

## SSH in

`openssh-server` is installed on both flavours but not started by default.

```sh
systemctl start ssh          # Debian
/etc/rc.d/rc.sshd start      # Slackware
```

Set a password you trust first — every Slax image in the world has the same `toor`.

Host keys regenerate on each boot unless you persist them, which means SSH clients will complain
about a changed key every time. Persist `/etc/ssh/` or bake keys in with a rootcopy overlay.
