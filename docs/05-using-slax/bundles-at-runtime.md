# Bundles on a running system

Slax's root filesystem is a stack of read-only squashfs **bundles** plus one writable layer. You can
add and remove bundles without rebooting.

## Where everything lives

After boot, the machinery is under `/run/initramfs/memory/`:

| | |
|---|---|
| `data/` | the boot medium — this is where `/slax/` is |
| `bundles/` | each `.sb` loop-mounted, one directory per bundle |
| `changes/` | the writable layer |
| `union/` | the merged result, which became `/` |
| `toram/` | only present when you booted with `toram` |
| `iso/` | only when booting from an ISO *file* |
| `modules/` | RAM store for bundles loaded with `slax activate` |

## Load order: higher number wins

`01-core`, `01-firmware`, `02-xorg`, … `05-chromium`. Each is inserted **above** the previous, so a
file in `05-chromium` shadows the same path in `01-core`. `savechanges` writes `99-changes-N.sb`
precisely so saved work outranks everything shipped.

Gaps are fine. Removing `05-chromium` does not require renumbering anything, and renumbering to
"tidy up" would silently change priorities.

## Loading a bundle now

```sh
slax activate /path/to/07-mytools.sb
slax list                      # what is loaded, in order
slax deactivate 07-mytools     # or just "mytools"
```

`activate` loop-mounts the file and splices it into the union immediately — no reboot. If the bundle
lives *inside* the union already, it is copied to RAM first, because you cannot mount a file from
the filesystem you are mounting it into.

**Deactivate fails if anything is still using the bundle.** Close the programs first; `dmesg` says
what is holding it.

⚠️ `slax activate` works **only with aufs**. Slax's own kernel has it, so this is fine on a stock
image — but a replacement kernel without the aufs patch silently falls back to overlayfs, where
branches cannot be added at runtime and these commands stop working.

## Saving your session as a bundle

```sh
savechanges                            # -> /slax/modules/99-changes-N.sb on the boot medium
savechanges /tmp/my-work.sb            # or somewhere specific
```

It squashes the writable layer into a new bundle, skipping the things that should not persist:
`/tmp`, `/proc`, `/sys`, `/dev`, `/run`, `/var/cache`, `/var/log`, `/etc/fstab`, `/etc/mtab`,
`/etc/resolv.conf`, X authority files, and aufs whiteouts.

Two things to know:

- **It stages through tmpfs**, so the whole changeset must fit in RAM.
- It is a *snapshot*, not continuous. Run it again and you get `99-changes-2.sb`.

This is the right tool for "I set this machine up how I like it, now make that permanent". For
continuous saving, use [persistence](persistence-perch.md) instead.

## Making a bundle from a directory

```sh
dir2sb /path/to/tree 07-mytools.sb     # directory -> bundle
sb2dir 07-mytools.sb                   # bundle -> directory (tmpfs-backed)
rmsbdir 07-mytools.sb                  # clean up after sb2dir
sb something                           # dispatcher: does the obvious thing
```

⚠️ `sb2dir` **mounts tmpfs over the target directory**, so a large bundle consumes RAM and a plain
`rm -rf` leaves a mount behind. Always use `rmsbdir`.

On Slackware, `txz2sb foo.txz` converts a Slackware package straight into a bundle.

Every one of these builds with the same parameters, which is what makes bundles interchangeable:

```sh
mksquashfs SRC DST -comp xz -b 1024K -Xbcj x86 -always-use-fragments
```

## Skipping a bundle at boot

No need to delete anything:

```
noload=05-chromium              # commas work: noload=04-apps,05-chromium
load=01-core|02-xorg            # the inverse whitelist -- note: NO comma support
```

`noload` is documented; `load` works but appears in no upstream documentation.

## Reserved paths inside a bundle

Upstream gives bundles four hooks, documented in `DOC/bundle.txt`:

| | |
|---|---|
| `/run/requires` | other bundles this one needs |
| `/run/activate.sh` | run when the bundle is activated |
| `/run/deactivate.sh` | run when it is removed |
| `/run/startcmd.sh` | run at desktop start |
