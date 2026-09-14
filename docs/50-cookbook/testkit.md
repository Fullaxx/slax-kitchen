# `testkit` — make a boot assertable

**Status: boot-verified** — applied on all four targets and boot-tested; the fact block below is real
output from a QEMU run.

```sh
kitchen apply <the recipe you want to prove> testkit
kitchen pack -s work/iso -o out/t.iso --force
kitchen test out/t.iso --kernel --seconds 300
```

Without this, a boot test can assert that livekit printed its three markers and reached a login
prompt. That proves the image boots. It does **not** prove that the recipe you just applied
actually changed anything.

`testkit` prints facts about the assembled filesystem to the console, where
[`qemu_boot.py --expect`](../60-testing/ci.md) can match them:

```
### TESTKIT BEGIN
union: aufs
bundles: 01-core.sb 01-firmware.sb 02-xorg.sb 03-desktop.sb 04-apps.sb 05-chromium.sb 08-kiosk.sb 08-locale.sb 08-network.sb 08-ssh.sb
kernel: 6.1.38
busybox: BusyBox v1.26.2
file /etc/timezone: Europe/Prague
file /etc/localtime: present 2301 bytes
file /root/.xinitrc: present 1020 bytes
file /etc/systemd/system/multi-user.target.wants/ssh.service: symlink -> /lib/systemd/system/ssh.service
### TESTKIT END
```

That single block boot-verifies four recipes: the tzfile `locale-timezone-keyboard` copied,
`kiosk-mode`'s 1020-byte `.xinitrc` beating 03-desktop's 13-byte one, `network-preseed`'s ConnMan
file, and — the one most worth seeing — **`enable-ssh`'s symlink surviving mksquashfs into the
union**.

## `union: aufs` is the headline

A kernel without aufs silently downgrades to overlayfs. `slax activate` stops working,
`union_append_bundles` stops behaving, and **nothing says why**. It is the exact failure mode
[`kernel.replace`](../00-overview/status.md) has to guard against, and this is the cheapest way to
see it.

## Why a rootcopy preinit, not a bundle

Instrumenting a boot normally means building a bundle — `mksquashfs` and an ISO repack for every
change to the instrumentation. rootcopy needs neither: `livekitlib` **sources** the script just
before `change_root`, so editing one small file is the whole edit loop.

And why *before* `change_root` rather than after boot: a post-boot reporter needs a systemd unit on
Debian and an rc script on Slackware — two mechanisms, two failure modes, and nothing at all if the
desktop never starts. `user_preinit` runs on both flavours identically, with the union fully
assembled at `$1`.

## What it proves, and what it does not

It reports **properties of the assembled filesystem**, which is exactly what a recipe changes. So
it proves the artifact is there, in the right place, with the right content.

It does **not** prove the feature works. `ssh.service` being correctly symlinked is not sshd
accepting a login; a 1020-byte `.xinitrc` is not the kiosk app appearing on screen. Those need a
full desktop boot — the host queue's Tier C. Cookbook pages say **"artifact boot-verified"** where
that is what was done, and the distinction is deliberate.

## Configuring it

`report` is a space-separated list of paths. Defaults cover what the branding, locale, ssh, TLS,
network and kiosk recipes change. Files of 200 bytes or less report their first line; anything
larger reports a size, so a CA bundle does not end up on the serial console.
