# `fix-slackware-bugs` — five confirmed upstream defects

**Status: verified** — applied on Slackware 64-bit and 32-bit; every fix checked in the built
bundle. **Not boot-tested.**

```sh
kitchen apply fix-slackware-bugs
```

**Slackware only.** Each fix targets a defect recorded in
[known upstream issues](../30-inventory/known-upstream-bugs.md).

## What it fixes

| issue | symptom | fix |
|---|---|---|
| **13** | no TLS client can verify any certificate | write `/etc/ssl/cert.pem` |
| **4** | `genslaxiso` fails — calls `genisoimage`, which is not installed | symlink to `mkisofs` |
| **5** | `pxe` fails — calls `busybox`, which is not in the running system | symlink to the initramfs copy |
| **7** | `nosound` is documented but does nothing | guard the `pamixer` line |
| **6** (half) | saved sessions carry empty `/media/<dev>` directories | one entry in `savechanges`' `EXCLUDE` |

### 13 — TLS is completely broken

The most consequential of the five. `01-core` ships 144 CA certificates in `/etc/ssl/certs` and
`OPENSSLDIR` is `/etc/ssl` — but **neither place OpenSSL actually looks exists**: no
`/etc/ssl/cert.pem` (the single-file default) and zero hash symlinks (the directory default).

`c_rehash` cannot create them because **Perl is not installed**, and `update-ca-certificates` writes
Debian's `certs/ca-certificates.crt` path, which this OpenSSL build never consults. So every TLS
client fails until the file exists. The fix concatenates the shipped PEMs into OpenSSL's own default
path — trusting nothing that was not already on the image.

### 4 and 5 — symlinks, not rewrites

Both are "the script calls a binary that is not installed". Slackware ships `mkisofs` and not
`genisoimage`; and there is no `/usr/bin/busybox` on a booted Slax, though there **is** one in the
initramfs, still mounted at `/run/initramfs`.

The busybox symlink follows a convention **the image itself established** — upstream already does
exactly this for `/usr/bin/vi` and `/usr/bin/nslookup`.

`pxe`'s `killall dhclient` on line 16 is left alone: it is a cleanup call with its errors already
redirected, so a missing `dhclient` costs nothing. Slackware uses `dhcpcd`, which `pxe` does not
stop — a real but separate defect.

### 7 — `nosound`

`-w` matters:

```sh
grep -q -w nosound /proc/cmdline || pamixer -u --set-volume 70
```

Without it, `nonosound` would also match — which is exactly the substring trap that makes issue 12
unfixable in this layer.

## Two things it does *not* fix, and why

**Issue 6's headline is a no-op.** `savechanges` greps `/etc/systemd/system/` for a
`Slax skip savechanges` marker, and that path does not exist on Slackware — but repointing the grep
would fix nothing. `slax-automount` writes `/etc/fstab`, which `savechanges` already excludes
unconditionally, and it never writes the marker anywhere. The *real* leftover is the mount-point
directories, so that is what gets fixed.

**Issue 12 is out of reach from a bundle.** `noautomount` is ignored because `fstab_create` tests
`grep -vq automount /proc/cmdline` and `noautomount` contains `automount`. `fstab_create` lives in
`livekitlib` inside `initrfs.img`, not in any bundle, and has already run before any bundle is
mounted. Use [`boot-cmdline`](boot-cmdline.md) to remove the parameter instead of negating it.

## Every patch verifies itself

A patch that cannot fail is worse than no patch, so each fix states the text it expects:

```sh
expect() {
    grep -qF "$2" "$1" || { echo "FAIL: $1 no longer contains: $2" >&2; exit 1; }
}
```

If upstream's text is gone, the recipe **fails loudly** rather than quietly patching nothing — the
same contract [`initramfs.patch`](../90-reference/verbs.md#initramfspatch--mknod)'s `expect_sha256`
provides. Patching in place also means no copy of upstream's scripts is carried here, so there is
nothing to keep current.

## Why it builds two bundles

`09-slackfix` carries the patched files; `10-slackpkg` carries `/etc/slackpkg/mirrors`.

They cannot be one bundle. **`BUNDLE_EXCLUDE` strips `etc/slackpkg/mirrors` from any computed
delta**, deliberately, so that `bundle.packages`' own build-time mirror pinning never leaks onto the
running system. A `sed` in the chroot is therefore silently discarded — which is what happened the
first time this recipe was written.

`bundle.files` is not filtered: it ships precisely what you list. The exclusion guards against
*accidental capture*, not deliberate inclusion, so a second bundle is the honest way to say "I meant
this one".

The mirror is architecture-specific — `slackware64-15.0` versus `slackware-15.0` — so there are two
guarded steps. Pointing a 32-bit image at the 64-bit tree would install packages it cannot run.
