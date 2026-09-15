# The reference container

The environment every measurement in `docs/` was taken on, and the one place the toolchain's
package list lives.

```sh
docker build -f containers/Containerfile -t slax-kitchen containers/
docker run --rm -it -v "$PWD:/work" slax-kitchen
```

`--device /dev/kvm` if the host has it, and nothing else. `bundle.packages` turned out to need only
`CAP_SYS_CHROOT` and `CAP_MKNOD`, both of which Docker grants by default, so `--cap-add SYS_ADMIN`
and `--security-opt seccomp=unconfined` are not needed for anything — see
[container vs host](../docs/40-workflow/container-vs-host.md).

Inside, `./kitchen doctor` should report 20 tools ok and both toolchain assertions passing. If it
does not, the image is wrong, not your checkout.

## The package list

`packages/*.txt` is the single source of truth, split by the CI job that installs it:

| file | installed by |
|---|---|
| `lint.txt` | `ci.yml` → `gates`, and this image |
| `build.txt` | `ci.yml` → `build`, and this image |
| `boot.txt` | `ci.yml` → `boot` (additive to `build.txt`), and this image |
| `dev.txt` | this image only — `7z`, `git`, `jq`, `curl`, `file`, `ssh`, and the squashfs decompressor fallbacks |

One package per line; `#` comments and blank lines are stripped. To install the whole set on a
machine that is not a container:

```sh
cat containers/packages/*.txt | grep -v -e '^#' -e '^$' \
  | xargs sudo apt-get install -y --no-install-recommends
```

**This used to live in four places** — the `gates`, `build` and `boot` jobs of `ci.yml` plus the apt
block in [toolchain](../docs/40-workflow/toolchain.md) — and they had already drifted apart.
`ci.yml` now reads these files, so there is no second copy to drift.

## Why the base image is a floating tag

`FROM ubuntu:24.04`, not a digest. That is a decision, not an oversight, and it is the one place in
this project where a download is *not* pinned by hash.

Everything else is pinned because it ends up inside an image that boots as root: base ISOs by
sha256 and byte size, apt signing keys by sha256, the busybox tarball by sha256, `vendor/linux-live`
by commit. **Nothing from this container is copied into the ISO.** `bundle.packages` chroots into
the bundle and runs Debian's own `apt` against Debian's own glibc — which is exactly why
`fakechroot` was rejected, since it `LD_PRELOAD`s host binaries against target libraries and
Ubuntu's glibc 2.39 cannot load against Debian's 2.36. The host userland does not leak in.

So what this image has to get right is that the tools work, and a rebuilt `ubuntu:24.04` does not
stop them working. In exchange the image **records what it actually installed**, at
`/etc/slax-kitchen-toolchain.txt`: build date, base `PRETTY_NAME`, python version, and every
package with its version. Any measurement in `docs/` can name what it ran on.

```sh
docker run --rm slax-kitchen head -5 /etc/slax-kitchen-toolchain.txt
```

## Why Ubuntu, and not just any distro

Four things constrain the base. None is a version pin; all four are satisfied by any current
Debian-family release.

| constraint | why |
|---|---|
| `/usr/lib/ISOLINUX/isohdpfx.bin` | hardcoded absolute path — `boot.isohybrid` needs it |
| `/usr/share/OVMF/OVMF_{CODE,VARS}_4M.fd` | hardcoded absolute path — the UEFI boot test needs them |
| `xz` supporting `--check=crc32` | the kernel's xz decoder cannot do CRC64, and CRC64 is xz's default |
| `squashfs-tools >= 4.2`, `python3 >= 3.9` | asserted by `kitchen doctor`; 3.9 for builtin generics |

The first two are Debian-family spellings; Fedora and Alpine put the same files elsewhere. Ubuntu
24.04 is also what the CI runner is, which is what keeps a CI-only failure from being possible.

## Two things this image cannot do

**`tools/build-busybox.sh` needs a docker daemon.** It compiles a static i386 busybox inside
`i386/alpine:3.19`, because this host has no 32-bit libc and no musl. No docker CLI is installed
here, so the script exits 2 with its own message. Run it on the host, or pass the socket in with
`-v /var/run/docker.sock:/var/run/docker.sock` and install the client.

**Bind mounts are resolved by the daemon, not by you.** If you run `docker` from *inside* another
container against a shared socket, `-v "$PWD:/work"` mounts the host's `$PWD`, which is usually not
the path you typed — you get an empty `/work` and `./kitchen: not found`. Give the host's path
instead. This is the same reason `build-busybox.sh` passes its script on stdin and takes the binary
back on stdout rather than bind-mounting anything.

**Also expect a git ownership refusal** when the checkout is owned by a different uid than the
container runs as — CI hits this, because the runner checks out as uid 1001 and the container is
root:

```sh
git config --global --add safe.directory /work
```

## What is deliberately absent

| | |
|---|---|
| `proot` | **unsafe.** It does not translate `statx()`, so `stat` escapes the fake root and reads the *host* filesystem. Silent and selective, so it can emit a corrupt bundle that looks fine. |
| `fakechroot` | fails outright on any glibc mismatch between host and target |
| `squashfuse`, `debootstrap` | nothing here needs them; `01-core.sb` is already a complete root filesystem |
