# `preinit-hook` — run your own code just before the system boots

**Status: verified** — applied on all four targets; the hook is installed at
`slax/rootcopy/run/preinit.sh` and the resulting ISO passes structure assertions. The script's
*runtime* behaviour has not been boot-tested.

```sh
kitchen apply preinit-hook
```

The last point at which you can change anything. `livekitlib`'s `user_preinit` runs this after
every bundle is mounted and the aufs union exists, but **before** `change_root` hands control to
systemd or init. The union directory arrives as `$1`, so the filesystem the machine is about to
boot into is right there and still writable.

## It is sourced, not executed

`livekitlib` does `. "$SRC" "$2"`. Three consequences, each easy to learn the hard way:

| | |
|---|---|
| **The shebang is decorative** | this runs in the initramfs's busybox `ash` whatever the first line says. Stay POSIX — no bashisms. |
| **`exit` ends the *boot*** | not the script. A stray `exit 1` in an error path gives a dead machine with no message. |
| **State leaks into init** | `cd`, exported variables and shell options outlive the script. |

So: **wrap anything that might fail in a subshell.** The shipped example does, and that is the
pattern to copy:

```sh
(
    mkdir -p "$UNION/var/log"
    ...
) 2>/dev/null || true
```

## What is actually available

Early boot, not the booted system. `/proc` and `/sys` are not yet what the running system will see,
and the toolset is the initramfs's: **busybox plus seven real binaries**. Do not expect GNU `awk`
or GNU `sed` — you get busybox applets, which are close but not identical.

See [the initramfs anatomy](../10-anatomy/initramfs.md) for exactly what is in there.

## The example

Writes a boot stamp to `/var/log/slax-preinit.log` in the union — deliberately the most boring
thing that is still a real demonstration. It proves the hook ran, proves `$1` is the union, and
cannot break a boot if it fails.

```
booted: 2026-09-14T10:42:01Z
cmdline: initrd=/slax/boot/initrfs.img vga=791 ...
bundles: 01-core 01-firmware 02-xorg 03-desktop 04-apps 05-chromium
```

## When to use something else

| you want | use |
|---|---|
| files on the live system, no code | [`rootcopy-overlay`](rootcopy-overlay.md) |
| to change what early boot itself does | [`initramfs-boot-timeout`](initramfs-boot-timeout.md) patches `/init` directly |
| a service that runs after boot | ship a unit or an `rc` script in a bundle |

`rootcopy.preinit` is the right tool only when you need the union *assembled but not yet booted* —
for example to rewrite a config based on hardware you can only see at runtime, or to refuse to boot
under some condition.
