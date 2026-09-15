# `branding` — hostname, version string and login banner

**Status: boot-verified** — applied and booted; the bundle mounts above `01-core` and its files win.

```sh
kitchen apply branding
```

```yaml
vars:
  hostname: slax-custom
  version: "Slax 12.2.0 (customized)"
  bundle: 07-branding
```

## What it changes

| file | stock | why it matters |
|---|---|---|
| `/etc/hostname` | `slax` | the shell prompt, and what the machine calls itself on the network |
| `/etc/slax-version` | `Slax 12.2.0 64bit` | shown by Slax's own tools |
| `/etc/issue` | the banner on tty1 | where `root` / `toor` is printed |

## Override, don't edit

All three live in `01-core.sb`. The recipe does **not** unpack and repack that 122 MiB bundle to
change three text files — it builds a 4 KiB `07-branding.sb`, and load order does the rest:

```
branch 0   changes            (writable)
branch 1   07-branding.sb     ← 4 KiB, wins
branch 2   05-chromium.sb
…
branch 7   01-core.sb         ← still byte-identical to the shipped one
```

Three things follow, and they are the reason this is the right shape for almost any file change:

- **The shipped bundles stay untouched**, so `kitchen probe` still reports them as stock and any
  difference it *does* report is genuinely yours.
- **Reversible by deleting one file.**
- **Skippable at the boot prompt** with `noload=07-branding`, without rebuilding anything.

`07` because branding adjusts the platform rather than adding content, which puts it in the
`00`–`09` band — [which numbers are whose](../10-anatomy/bundles-squashfs.md#which-numbers-are-whose).
It is a var, so a profile can move it:

```yaml
  - name: branding
    vars: {bundle: 20-branding}
```

That did not work until recently: the number was written inline in the recipe, and overriding a var
a recipe does not declare is rejected. `branding` was the one shipped recipe the override advice in
[recipes in a fork](../40-workflow/recipes-in-a-fork.md) did not apply to.

## On leaving the password in the banner

The shipped `/etc/issue` prints `root` / `toor` in yellow, and this recipe keeps it. That is
deliberate: the password is public knowledge in every Slax release, and removing it from the banner
makes the image harder to use without making it any harder to break into. If the credentials matter
for your build, change them — a `users-and-auth` recipe is the right tool, and hiding the banner is
not a substitute for it.

## Extending it

`bundle.files` takes `src:` as well as `content:`, and a directory source is copied recursively, so
wallpaper and a Fluxbox style go in the same bundle:

```yaml
- verb: bundle.files
  bundle: 07-branding
  files:
    - {dest: /etc/hostname, content: "myhost\n"}
    - {dest: /usr/share/wallpapers/mine.png, src: ./mine.png}
    - {dest: /root/.fluxbox/styles/mine, src: ./fluxbox-style}
```

Anything with a path can be overridden this way. What it **cannot** do is change files that do not
exist in a bundle at all — `/etc/fstab` is generated at boot by `fstab_create`, so a copy in a bundle
is simply ignored. See [runtime-layout](../10-anatomy/runtime-layout.md).

## Verifying

```sh
kitchen pack && kitchen test out/*.iso --kernel --seconds 240
```

The boot log lists what mounted, in order:

```
* modules/01-core.sb
…
* modules/07-branding.sb
* Setting up empty union using aufs
```

On the booted system, `slax list` shows the stack and `cat /etc/hostname` shows which copy won.
