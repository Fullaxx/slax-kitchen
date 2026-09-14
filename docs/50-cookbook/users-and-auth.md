# `users-and-auth` — replace the public root password

**Status: matrix-verified** — applied on Debian and Slackware; `/etc/shadow` checked after unpacking, all
24 accounts intact with only `root` changed and `slaxuser` added. **Not boot-tested.**

```sh
kitchen apply users-and-auth
```

```yaml
vars:
  root_hash: "$6$…"      # openssl passwd -6
  username: slaxuser
  user_hash: "$6$…"
```

Stock Slax logs in as `root` / `toor`, and `/etc/issue` prints that on tty1. That is a reasonable
default for a live system you boot on your own hardware, and a bad one for an image you hand out or
put on a network — especially alongside [`enable-ssh`](enable-ssh.md).

## Never a plaintext password

`vars` take a **hash computed on the build host**:

```sh
openssl passwd -6
```

The shipped default is the hash of the literal string `change-me-now`, published here on purpose:
it is an example, not a secret, and an image built without changing it is no more private than
stock Slax.

`$6$` (SHA-512 crypt) is used rather than Debian's native `$y$` (yescrypt) because `$6$` is accepted
by libcrypt on **both** flavours, so one hash format works everywhere. Slackware uses `$6$`
natively.

## Why this needs a chroot

The obvious approach — ship `/etc/shadow` in a bundle — is wrong. **A bundle's copy replaces the
whole file**, so every account it does not list disappears: `guest`, `sshd`, `messagebus`, and the
twenty-odd system accounts. You would have to reproduce the shipped file exactly, per flavour, and
keep it current.

Running `chpasswd -e` in a chroot edits the real file and leaves everything else alone. The bundle
delta then carries the correct result:

```
$ grep -c : etc/shadow        24        # same as stock
root:$6$kitchenXY$opFFZajCQU            # changed
guest:$y$j9T$C1GleiUryp7C4K6            # untouched, still yescrypt
slaxuser:$6$kitchenXY$opFFZa            # added
sshd:!:19639::::::                      # system accounts intact
```

`-e` means "this value is already a hash", so no plaintext is written to disk, passed on a command
line, or recorded in the recipe.

## Do not delete the `guest` account

`guest` (uid 1000) is baked into `01-core` on both flavours and **is load-bearing**. It exists
because Chromium refuses to run as root — and `/root/.fluxbox/startup` bind-mounts
`/home/guest/{Desktop,Documents,Downloads,…}` over `/root/`, so removing the account breaks the
desktop's XDG directories as well as the browser.

This recipe leaves it exactly as shipped: locked on Slackware, passworded on Debian.

## It also rewrites `/etc/issue`

Leaving the `root` / `toor` banner up after changing the password is worse than not changing it —
it tells every user a credential that no longer works. The recipe replaces `/etc/issue` with a bare
`Slax \l`.

[`branding`](branding.md) owns that same file. **Apply one or the other**, not both — whichever
runs second wins, and if they land in different bundles the higher number wins.

## What ends up in the bundle

Ten files: the six account databases, `/etc/issue`, and the new user's three skel dotfiles.

`useradd` and `chpasswd` also leave `/etc/passwd-`, `/etc/shadow-`, `/etc/group-`, `/etc/gshadow-`,
`/etc/subuid-`, `/etc/subgid-` and `/etc/.pwd.lock` behind — the backups hold the state **before**
the change, so a recipe whose whole purpose is changing `/etc/shadow` would otherwise ship the old
one beside the new one. `BUNDLE_EXCLUDE` now strips all seven.
