# `tor-browser` — Tor Browser instead of Chromium

**Status: matrix-verified** — the recipe builds and the ISO's structure is asserted. Nobody has
yet booted this image and used the browser, so it stays on this rung. The rung that matters for
this recipe is `runtime-verified`, and the [last section](#what-would-move-this-to-runtime-verified)
says exactly what has to be seen to claim it.

```sh
kitchen apply tor-browser
# or the whole image:
kitchen build tor
```

Installs the system `tor` daemon, unpacks a **pinned** Tor Browser
tarball to `/opt/tor-browser`, and points the desktop at it.

Slax's own Chromium stays unless something removes it, and for this image you almost certainly want
it gone — a browser that leaks beside one that does not is a strange image. That is a separate
recipe, listed first, which is what [`profiles/tor.yaml`](../../profiles/tor.yaml) does:

```yaml
recipes:
  - name: remove-bundle
    vars: {drop: "^05-chromium\\.sb$"}
  - tor-browser
```

> **CI builds this weekly, not on every push.** The tarball is an external dependency with a pinned
> sha256, so a Tor Browser release fails the build *by design*. Running it per-push would turn the
> Tor Project shipping a security update into a red master. `ci/upstream-watch.sh` checks the
> version every Monday and files an issue instead. See [ci.md](../60-testing/ci.md).

## There is no Tor Browser package, anywhere

This is the first thing to understand, because every other route looks easier and none of them work:

| route | what you actually get |
|---|---|
| `deb.torproject.org` | `tor`, `tor-geoipdb`, a keyring — **the daemon, not the browser** |
| `apt install torbrowser-launcher` | a *downloader*. The browser arrives on first run, over the network, into the live overlay — **every boot**, without persistence |
| the `.tar.xz` from dist.torproject.org | the browser. This recipe |

The launcher is the tempting one and it is the worst fit here: an offline boot gets a launcher that
cannot launch, and an offline boot is most of what a USB stick is for.

So the tarball, pinned by sha256 taken from the Tor Project's **signed** `sha256sums-signed-build.txt`
rather than scraped off the download page. Version and hash live in the recipe's `vars:`.

## 64-bit only, and that is upstream's decision

Tor Browser 15.0.x ships **both** `linux-x86_64` and `linux-i686`, so a 32-bit build would work
today. That is exactly the trap. The Tor Project has said 15.0 is the **last** major release to
support 32-bit Linux; 16.0 drops i686 and is already at `16.0a9` (read 2026-09-17). Declaring
`arch: [32bit, 64bit]` would be green now and break at the next bump with nothing to explain why.

## Removing Chromium costs the browser nothing — measured

The removal is [`remove-bundle`](remove-bundle.md)'s job, not this recipe's, but the question it
raises belongs here. `05-chromium.sb` is not just Chromium. Its package database carries **24 more packages** than
`04-apps`, and they are the shared browser runtime: `libnss3`, `libnspr4`, `libevent-2.1-7`,
`libopus0`, `libflac12`, `libvorbis`, `libpulse0` and the rest. The obvious worry is that taking
the bundle away breaks Tor Browser.

It does not, and the reason is that Tor Browser ships its own copies. Reading `DT_NEEDED` out of
`firefox.real`, `libxul.so` and `TorBrowser/Tor/tor` in the 15.0.23 tarball:

| | |
|---|---|
| sonames needed | **47** |
| shipped inside the bundle | **17** — and they are exactly the ones at risk: `libnss3`, `libnspr4`, `libnssutil3`, `libssl3`, `libsmime3`, `libplc4`, `libplds4`, `libcrypto.so.3`, `libssl.so.3`, `libevent-2.1.so.7`, `libstdc++.so.6` |
| resolved from the image | **30**, every one of them in `01-core`, `02-xorg` or `03-desktop` — all of which survive |

So this recipe installs **no** replacement libraries. An earlier draft named `libpulse0` "for
audio"; the measurement says Tor Browser links `libasound.so.2` — already in `01-core` — and never
names libpulse at all.

## Running as `guest`, without patching upstream

Slax runs its desktop as **root**, and `start-tor-browser` refuses to start as root — a hard
`exit 1`, not a warning. The usual workaround is to `sed` that check out. This recipe does not,
because running an anonymity tool as root defeats a protection upstream added on purpose.

Slax already solved this for Chromium, which refuses for the same reason. `/usr/bin/fbliveapp`
drops to uid 1000 `guest`, an account baked into `01-core` **precisely for this**:

```sh
if [ "$GUEST" = "true" -a "$EUID" -eq 0 ]; then
   xhost + >/dev/null 2>/dev/null
   exec su -c "$EXECUTABLE "$@""  guest
fi
```

`/usr/local/bin/tor-browser` is those three lines, with one change: `xhost +SI:localuser:guest`
rather than `xhost +`, so the display opens to one account instead of every local and remote
client for the rest of the session. It falls back to `xhost +` if the SECURITY extension is
missing, which is no worse than stock.

### The empty file that makes a read-only install work

A bundle is a read-only squashfs branch, but Tor Browser keeps its profile and tor state **inside
its own tree**. That would be fatal — except upstream already handles it
(`start-tor-browser:214-222`):

```sh
if test -f "$browser_dir/is-packaged-app"; then
  system_install=1
  browser_home="$HOME/.tor-browser"
```

So the recipe ships an **empty** `is-packaged-app` beside `start-tor-browser`, and every writable
thing goes to `~guest/.tor-browser`. No bind mount, no copy of the tree, no patch. Verified by
running it as uid 1000 with the marker present: `$HOME/.tor-browser` appeared containing
`.tor project/firefox` and `.tor project/Tor`, and **not one file under the install tree was
written**.

### ⚠️ Budget the RAM

That state reached **39 MiB** after a single launch. Without persistence the union's writable
branch is RAM, so it is 39 MiB of memory that the running system does not get — more as you
browse, because the cache lives there too. On a 2 GiB machine this is fine; on a 512 MiB one it is
not. Boot with [perch](../10-anatomy/union-and-persistence.md) if you want the profile to survive,
which for Tor Browser also means your bookmarks and your `torrc` survive — decide whether you want
that before enabling it.

## Permissions: why `world_readable: true` is not tidiness

The Tor Browser tarball is `0700` on **all 27** of its directories and `0600` on 185 of its 220
files. There is not one group or world bit in the archive. `bundle.fromTarball` packs with
`-all-root`, which rewrites *ownership* to root and leaves *mode bits* exactly as the archive set
them — so without intervention the bundle is root-only and `guest` cannot even traverse
`/opt/tor-browser`.

`world_readable: true` mirrors the owner's read bit to group and other, and the execute bit only
where the owner already has it. Measured on this archive: **247 paths widened**, directories
`0700 → 0755`, executables `0700 → 0755`, data `0600 → 0644`, and the count of executable files
stays at **35** — no data file becomes runnable. setuid and setgid are never added and cannot be:
only the low `0o055` bits are ever OR-ed in, and the verb already refuses setuid members outright.

It is opt-in per step and prints what it touched. Silently widening permissions on somebody else's
archive is exactly the kind of quiet behaviour this project refuses.

## The desktop entry keeps Chromium's filename

`usr/share/applications/5chromium.desktop` is **replaced**, not added alongside. A bundle can add
and replace but [cannot delete](../40-workflow/composing-bundles.md), so replacing it in place is
the only way to remove the stock **Web Browser** button — which otherwise runs `fbliveapp chromium`
and, with the bundle gone, offers to `apt install chromium`. Offering to install Chromium is a poor
look on a privacy image.

`root/.fluxbox/menu` is the stock file with **exactly one line changed**, verified by diff:

```diff
-   [exec] (Web Browser) { fbstartupnotify && fbliveapp chromium --no-default-browser-check }
+   [exec] (Tor Browser) { fbstartupnotify && tor-browser }
```

It is a **copy**, pinned to Slax 12.2.0's menu. If Slax ships a new menu, this file will quietly
hold the old one back — refresh it then.

## The daemon is not the browser

`tor`, `tor-geoipdb` and `torsocks` are installed for the rest of the system. Tor Browser carries
its own `tor` and works with none of them present; these are what let you do:

```sh
torsocks curl https://check.torproject.org
```

They come from **bookworm**, not `deb.torproject.org`. The Tor Project's repository has a newer
daemon, and taking it would add a fourth party to what the image trusts, forever, for a daemon the
browser does not use.

## What would move this to `runtime-verified`

Everything above is a claim about files being in the right place. The rung this recipe needs is a
person watching it work:

```sh
tools/qemu/boot.py out/slax-tor-12.2.0.iso --bios --display vnc
```

Then, in the desktop: launch **Tor Browser** from the menu, confirm it connects to the Tor network,
and check it is not running as root —

```sh
ps -o user= -C firefox.real     # must print `guest`
torsocks curl https://check.torproject.org | grep -i 'congratulations\|not using tor'
```

A browser whose entire purpose is the network it reaches cannot be called verified because its
files unpacked.
