# `enable-ssh` — turn on the sshd that is already there

**Status: artifact boot-verified.** Built and structurally asserted on all four
targets; then booted under QEMU with [`testkit`](testkit.md), which confirmed the enablement symlink reached the union intact (`symlink -> /lib/systemd/system/ssh.service`).
**Runtime behaviour is still untested** — that the file is correct is not the same as
the feature working, and the difference needs a full desktop boot.

```sh
kitchen apply enable-ssh
```

## Nothing is installed

`/usr/sbin/sshd`, `/etc/ssh/` and the PAM file are all in the stock `01-core` on **both**
flavours — 1.26 MB of OpenSSH on Debian, 1.25 MB on Slackware. Slax simply never enables it.

So this is a switch, not a package: **no chroot, no network, `privilege: none`**.

## How each flavour is switched on

| | |
|---|---|
| **Debian** | `ssh.service` is *disabled, not masked*. There is no `multi-user.target.wants/ssh.service` in any bundle, and `ConditionPathExists=!/etc/ssh/sshd_not_to_be_run` passes because that file does not exist either. The bundle ships exactly the symlink `systemctl enable` would create. |
| **Slackware** | the switch is the executable bit on `/etc/rc.d/rc.sshd`, which ships `0644`. |

The Debian half uses `bundle.fromDir` rather than `bundle.files`, because **the artifact is a
symlink** and `bundle.files` writes only regular files. mksquashfs preserves symlinks, and so does
git, so a committed symlink round-trips correctly.

The Slackware half does **not** flip the bit on `rc.sshd`. A recipe cannot `chmod` a file it is not
replacing, and replacing it would mean shipping a copy of upstream's 1814-byte script and
inheriting the job of keeping it current. `/etc/rc.d/rc.local` is the documented extension point,
ships as a 274-byte comment-only stub, and `rc.M` runs it last — so that is what the bundle
replaces.

## The Slackware trap: a daemon nobody can log into

Slackware's `sshd_config` has **no `Include` directive and no `/etc/ssh/sshd_config.d/`**, so a
drop-in fragment is impossible. Its entire non-comment content is three lines:

```
AuthorizedKeysFile	.ssh/authorized_keys
UsePAM yes
Subsystem	sftp	/usr/libexec/sftp-server
```

With no explicit `PermitRootLogin`, **OpenSSH 9.5 defaults to `prohibit-password`**. Enabling sshd
and nothing else therefore gives you a daemon that `root` — the only account with a password —
cannot log into. The recipe replaces `sshd_config` wholesale, keeping those three lines verbatim
and adding `PermitRootLogin yes`.

Debian needs no equivalent change: Slax already ships
`/etc/ssh/sshd_config.d/root_login_enable.conf`.

## Host keys are not shipped, deliberately

Both flavours generate them on first start — Debian via the unit's `ExecStartPre`, Slackware via
`ssh-keygen -A` in the `rc.local` snippet.

Baking a private host key into an image would mean **every machine booted from it shares one
identity**, which defeats the point of host keys, and would put a private key in a public
repository. If you need host keys stable across boots, that is what
[persistence](../05-using-slax/persistence-perch.md) is for.

## Before you ship this

The root password is still `toor` and is **printed on tty1** by `/etc/issue`. An image with sshd
enabled and a published password is an open machine. Apply
[`users-and-auth`](users-and-auth.md) as well, or do not enable sshd.
