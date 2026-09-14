# `rootcopy-overlay` — drop files onto the live system, no rebuild

**Status: runtime-verified** — both the file copy and the preinit hook confirmed firing at boot.

```sh
kitchen apply rootcopy-overlay
```

The cheapest customization Slax offers, and the only one that touches no squashfs at all.

## How it works

livekit's `copy_rootcopy_content()` runs `cp -a <data>/rootcopy/* <union>` just before
`change_root`, so **anything under `/slax/rootcopy/` on the boot medium lands on the running
filesystem**. Upstream ships no rootcopy directory — livekit looks for one and silently skips when
it is absent — so this recipe creates it.

Two properties worth understanding:

- **It is a copy, not a union branch.** Files land in the writable layer, so they beat *every*
  bundle including `99-changes-*.sb`, and they do not appear in `slax list`.
- **On a writable medium it needs no rebuild at all.** Edit `/slax/rootcopy/` on the USB stick
  itself and the change takes effect on the next boot. The recipe is really just a convenient way to
  populate that directory when you are building an ISO.

## The preinit hook

`/slax/rootcopy/run/preinit.sh` is livekit's official pre-boot hook. `user_preinit()` **sources** it
(`. "$SRC" "$2"`) with the assembled union directory as `$1`, after the bundles are mounted but
before the real init starts — the last point at which you can touch the filesystem the system is
about to boot into.

Because it is *sourced* rather than executed:

- a shebang is decorative;
- `cd`, exported variables and especially `exit` affect the initramfs shell itself, and `exit` will
  abort the boot. Wrap anything risky in a subshell.

Its output appears inline in the boot messages, which also makes it the simplest way to prove
rootcopy fired at all.

## Verified at boot

```
* Adding bundles to union
* Copying content of rootcopy directory...          <- livekit
* Executing user custom preinit...                  <- livekit
* rootcopy-overlay: preinit running, union is at /memory/union
* rootcopy-overlay: wrote /etc/slax-kitchen-stamp
Live Kit done, starting slax
```

The first two lines are livekit's; the last two are the recipe's own script. Note `$1` really is
`/memory/union`, which is the union before `pivot_root` moves it to `/`.

## When to use this instead of a bundle

| | rootcopy | bundle |
|---|---|---|
| Rebuild needed | none | `mksquashfs` |
| Editable on the stick | **yes** | no |
| Survives `savechanges` | it is re-applied every boot | yes |
| Good for | config files, keys, wallpapers, small scripts | packages, anything large |
| Bad for | anything big — it is copied into RAM every boot | quick tweaks |

Large files are the trap: rootcopy content is copied into the union on **every** boot, so a big
payload costs both boot time and RAM. Use a `07-*.sb` bundle for that.
