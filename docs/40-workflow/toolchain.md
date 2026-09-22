# Tools

Everything this toolkit needs, what each one is for, and what breaks without it.

```sh
./kitchen doctor
```

`doctor` checks each tool, names the apt package for anything missing, and reports what the current
machine can actually run. `kitchen apply` also runs a **preflight** before touching anything, so a
recipe whose tools are absent fails before it has half-applied — see [below](#preflight).

## One command

Debian / Ubuntu:

```sh
cat containers/packages/*.txt | grep -v -e '^#' -e '^$' \
  | xargs sudo apt-get install -y --no-install-recommends
```

The list itself lives in [`containers/packages/`](../../containers/README.md), split by the CI job
that installs it — `lint.txt`, `build.txt`, `boot.txt`, `dev.txt`, one package per line with a
comment saying why it is there. `.github/workflows/ci.yml` and the reference container read the
same files.

**It used to be written out here as well**, and in three places in `ci.yml`, and the four copies had
drifted: this page's list was the superset, and CI was missing `p7zip-full`, `qemu-utils`, `git`,
`jq`, `curl`, `ca-certificates` and `file`. Nothing detected it, because nothing on a runner asked
for those tools. One list is why that cannot recur.

Or skip the question entirely and use the image the list builds:

```sh
docker build -f containers/Containerfile -t slax-kitchen containers/
docker run --rm -it -v "$PWD:/work" slax-kitchen
```

## What each is for

### Bundles

| | package | |
|---|---|---|
| `mksquashfs` | `squashfs-tools` | build `.sb` bundles |
| `unsquashfs` | `squashfs-tools` | read them — **in place, via `-o <offset>`**, without extracting the ISO |

Version matters less than you would expect: the format is squashfs 4.0 with xz, and every
`squashfs-tools` since 4.2 produces it. What matters is passing upstream's exact four flags — see
[edit-bundles](edit-bundles.md).

### initramfs

| | package | |
|---|---|---|
| `cpio` | `cpio` | SVR4/newc archive |
| `xz` | `xz-utils` | **must support `--check=crc32`** — the kernel's xz decoder cannot do CRC64, and the default is CRC64 |

### ISO

| | package | |
|---|---|---|
| `xorriso` | `xorriso` | the default backend. Only one of the two that can add a second El Torito entry, so UEFI needs it. **Boot tests need it too, wherever they run.** It takes the kernel out of the image for `--kernel` and `--persistence`, and reads the boot menu for `--bios`, `--uefi` and `--usb`. Without it they refuse rather than boot |
| `genisoimage` | `genisoimage` | upstream's own tool; kept so a rebuild can be byte-compared against theirs |
| `isohybrid` | `syslinux-utils` | MBR + GPT so the ISO can be `dd`'d |
| `7z` | `p7zip-full` | reads ISO9660 and squashfs without mounting. **Do not unpack with it** — it drops Rock Ridge modes |

### Bootloader

| | package | |
|---|---|---|
| `extlinux` | `extlinux` | install the USB/HDD loader. **A separate apt package from `syslinux`** — easy to miss |
| `syslinux` | `syslinux` | BIOS tooling |
| — | `syslinux-common`, `isolinux` | supply `isohdpfx.bin` (needed by `isohybrid`) plus `hdt.c32`, `memdisk`, `chain.c32` |

### UEFI — the unprivileged trick

| | package | |
|---|---|---|
| `grub-mkstandalone` | `grub-efi-amd64-bin` | build a self-contained `BOOTX64.EFI` that can read ISO9660 |
| `mkfs.vfat` | `dosfstools` | **create** a FAT image as a plain file (`-C`) |
| `mmd`, `mcopy` | `mtools` | write into that image **without mounting it** |

Those last two rows are the reason `uefi-bootable` works with no privileges at all. There is no loop
device and no mount anywhere in the path.

GRUB rather than the shipped `syslinux.efi` because `syslinux.efi` implements FAT and nothing else —
it cannot read the ISO9660 filesystem the kernel lives on. See
[the UEFI gap](../20-boot-sequence/uefi-cd-gap.md).

### Testing

| | package | |
|---|---|---|
| `qemu-system-x86_64` | `qemu-system-x86` | boot tests. Works without KVM, 10–20× slower |
| `qemu-img` | `qemu-utils` | qcow2 disks for [`tools/qemu/boot.py`](../60-testing/qemu.md) |
| `mkfs.ext4` | `e2fsprogs` | the disk `kitchen test --persistence` boots twice onto, and `boot.py --disk-create ext4`. Lives in `/usr/sbin`, which a non-login PATH can lack; every caller looks there too |
| — | `ovmf` | UEFI firmware. Copy `OVMF_VARS` to a **private writable** file first; the packaged one is read-only |

**All four are needed where the boot happens, which need not be here.** With a
[boot host](../60-testing/boot-host.md) configured, they are required on that machine and
checked there before it starts; what this machine needs instead is the row below.

| | package | |
|---|---|---|
| `ssh` | `openssh-client` | reach the boot host. Key-based login only — nothing ever prompts |
| `rsync` | `rsync` | send the tree and the image there, and bring the evidence back |

### Gates and plumbing

| | package | |
|---|---|---|
| `shellcheck` | `shellcheck` | commit gate 30 |
| `yamllint` | `yamllint` | commit gate 40 |
| `python3-yaml`, `python3-jsonschema`, `python3-pyflakes` | — | recipe and profile validation, and commit gate 35 |
| `jq`, `curl`, `file`, `git` | — | JSON, `kitchen fetch`, artifact identification, submodules. `git` is also what lists the files sent to a boot host |

## Two installation gotchas

**`extlinux` is its own package.** Installing `syslinux` does not give you `extlinux`, and the
failure only appears when a recipe reaches for it.

**A Python venv cannot see apt-installed modules.** If `python3` resolves to something under
`/opt/venv` or similar, `python3-yaml` and `python3-jsonschema` from apt are invisible to it:

```sh
pip install PyYAML jsonschema
```

`kitchen doctor` catches this — it imports the modules rather than checking for the apt package,
the same way `lib/validate.py` does:

```
python3 imports yaml + jsonschema: NO -- yaml jsonschema not importable
```

## Not required

| | |
|---|---|
| `proot` | ⚠ **do not use.** It does not translate `statx()`, so `stat` reads the host filesystem from inside the fake root. Rejected; no longer listed by `doctor`. [container vs host](container-vs-host.md) |
| `fakechroot` | fails on any glibc version mismatch between host and target |
| `squashfuse`, loop devices, `/dev/fuse` | nothing here needs them |
| `debootstrap` | never needed — `01-core.sb` is already a complete root filesystem |

## Preflight

`kitchen apply` resolves every tool, file and capability the **whole plan** needs before running the
first step:

```
$ ./kitchen apply uefi-bootable memtest86plus
preflight: grub-mkstandalone not installed (apt-get install grub-efi-amd64-bin)
```

This exists because of a real failure: with the checks inside each verb, three recipes applied
successfully and the fourth then failed on a missing tool, leaving a half-modified tree and a
downloaded payload. The requirement map is `VERB_REQUIRES` in `lib/apply.py`.

`kitchen build` preflights before it even unpacks, so a missing tool costs you nothing.

## Capabilities, not just tools

Having the binaries is not sufficient for everything. `doctor` reports both:

```
Capabilities
  no   mount        no CAP_SYS_ADMIN
  no   userns       blocked by the container runtime, not the kernel
  no   kvm
  ok   mknod
  ok   chroot
```

| | needs |
|---|---|
| everything except the two below | nothing special |
| `bundle.packages` | `chroot` + `mknod` |
| the full boot matrix | `/dev/kvm` — a speed problem, not a capability one |

Details and what to do about it: [container vs host](container-vs-host.md) and
[host handoff](host-handoff.md).
