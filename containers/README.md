# The reference container

The environment every measurement in `docs/` was taken on, and the one place the toolchain's
package list lives.

```sh
docker build -f containers/Containerfile -t slax-kitchen containers/
docker run --rm -it -v "$PWD:/work" slax-kitchen
```

On Debian 12 instead, which is equally supported and equally tested:

```sh
docker build -f containers/Containerfile --build-arg BASE=debian:12 \
             -t slax-kitchen:deb12 containers/
```

`--device /dev/kvm` if the host has it, and nothing else. `bundle.packages` turned out to need only
`CAP_SYS_CHROOT` and `CAP_MKNOD`, both of which Docker grants by default, so `--cap-add SYS_ADMIN`
and `--security-opt seccomp=unconfined` are not needed for anything — see
[container vs host](../docs/40-workflow/container-vs-host.md).

Inside, `./kitchen doctor` should report every tool ok and every toolchain assertion passing. If it
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
by commit. **Almost nothing from this container is copied into the ISO** — the exceptions are
[listed below](#what-the-base-actually-changes-in-the-iso). `bundle.packages` chroots into
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

## Which base, and why the default is Ubuntu

Four things constrain the base. None is a version pin; all four are satisfied by any current
Debian-family release — **Debian included**.

| constraint | why |
|---|---|
| `/usr/lib/ISOLINUX/isohdpfx.bin` | hardcoded absolute path — `boot.isohybrid` needs it |
| `/usr/share/OVMF/OVMF_{CODE,VARS}_4M.fd` | hardcoded absolute path — the UEFI boot test needs them |
| `xz` supporting `--check=crc32` | the kernel's xz decoder cannot do CRC64, and CRC64 is xz's default |
| `squashfs-tools >= 4.2`, `python3 >= 3.9` | asserted by `kitchen doctor`; 3.9 for builtin generics |

Those rule out Fedora and Alpine, which put the first two files elsewhere. They do **not** rule out
Debian 12, which ships both at the identical paths.

**The default is `ubuntu:24.04` for exactly one reason: it is what `runs-on` gives us.**
`.github/workflows/ci.yml` installs the toolchain on the *runner*, not in this image, so matching
the runner is what keeps a CI-only failure impossible. That is the whole argument — there is no
technical advantage to Ubuntu here.

**And there is a real argument the other way**, which is worth knowing: upstream builds Slax on a
*running Debian 12*. There is no separate build host in the official build at all —
`vendor/linux-live/Slax/README` says *"Use only on freshly installed OS"*, and
`Slax/debian12/install` installs `squashfs-tools genisoimage … xz-utils` into the image, because the
image is the build host. Debian 12's toolchain is literally what produced the stock bundles.

CI builds **both** on every push and runs `doctor --strict` plus all thirteen gates in each, so
"any Debian-family release works" is a tested claim rather than an assumption.

### One file, and when to stop

This stays a single `Containerfile` with a `BASE` argument only while both bases need the identical
`RUN` steps. Today they do: every package name in `packages/*.txt` is a Debian name Ubuntu
inherited, and nothing in the body branches.

If they ever diverge — a package pinned on one, different GRUB or `squashfs-tools` handling, a
workaround that applies to one and not the other — **do not add a conditional on `$BASE`.** Split
into `Containerfile.Ubuntu.24.04` and `Containerfile.Debian.12`, and point the CI matrix at the two
files. A Containerfile that branches on its own base is two Containerfiles wearing a trench coat,
and it hides the divergence instead of recording it.

**Moving the base moves the tools, and some tests hold a copy of what a tool printed.** Those
name the version they were captured from, so `grep -rn 'Captured from:' tests/` is the list to
re-derive after a bump — checked against the toolchain list above, which records what each build
actually had. The reasoning is in
[what a test here is for](../CONTRIBUTING.md#what-a-test-here-is-for).

### What the base actually changes in the ISO

Three things from this container can end up inside the shipped image. Two run before the kernel:

| what | from | how it gets in |
|---|---|---|
| `BOOTX64.EFI` | the host's GRUB | `grub-mkstandalone` → `boot/efi.img` → El Torito alt-boot entry |
| the isohybrid MBR | the host's syslinux | `isohdpfx.bin` → `xorriso -isohybrid-mbr` → the ISO's first 432 bytes |
| `/etc/localtime` | the host's tzdata | [`locale-timezone-keyboard`](../docs/50-cookbook/locale-timezone-keyboard.md) copies the host's tzfile, since a recipe cannot reference a path inside the tree it is building |

`kitchen sources` names all three with the host package they came from, and where its source is.

Measured across the two bases:

| | ubuntu:24.04 | debian:12 |
|---|---|---|
| `grub-efi-amd64-bin` | 2.12 | **2.06** |
| `BOOTX64.EFI` from our module list | 6,193,152 B | **10,334,208 B** |
| `isohdpfx.bin` | `7b43a4dd…` | `7b43a4dd…` — **byte-identical** |
| `squashfs-tools` | 4.6.1 | 4.5.1 |
| `genisoimage` | 1.1.11 | 1.1.11 |

So the MBR does not vary, and the EFI binary does: a Debian 12 build carries GRUB 2.06 and an ESP
about 4 MB larger. Both boot — see [uefi-bootable](../docs/50-cookbook/uefi-bootable.md).

What does *not* vary is the userland inside a bundle: `bundle.packages` chroots in and runs
Debian's own `apt` against Debian's own glibc, so the host's libraries never reach the image.

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

## Two things this image is not, and why

**It is not published anywhere.** No registry, no `ghcr.io`, no tag you can pull. CI builds it,
proves it, and throws it away. That is a decision, not an oversight: the build is ~44 s on a runner
and ~20 s locally, so publishing would save nothing measurable, while costing a third write
permission on a repository that currently has two (`contents: write` in the release job,
`issues: write` in upstream-watch), plus a published artifact with its own tagging and lifecycle to
keep honest. Revisit if the build gets slow, or if CI starts running jobs inside the image.

**CI does not run inside it.** `ci.yml` installs `packages/*.txt` on the `ubuntu-24.04` runner and
runs the `container` job beside the others. That is the only reason the default base is Ubuntu —
so moving CI inside this image is the change that would make the base a genuinely free choice, and
`debian:12` a coherent default.

Nothing structural blocks that. `ci/recipe-matrix.sh` already branches on `id -u`, and a GitHub
`container:` job runs as root, so it would take the no-sudo path with no edit. The parts that would
need re-verifying are the boot job's QEMU and the `actions/cache` steps. It is not queued; it is
written down so the Ubuntu default stays a choice somebody can revisit rather than an accident.
