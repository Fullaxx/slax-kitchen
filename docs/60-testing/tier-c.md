# Tier C — every boot path, on a machine that can boot

Tier A parses the built ISO. Tier B boots the kernel directly and asserts that livekit
init ran. **Tier C boots the image the way a machine would** — through a bootloader, off a
USB device, and twice onto the same disk to prove persistence.

It needs `/dev/kvm`, which GitHub-hosted runners do not have, so CI can exercise the
harness but can never be the evidence. That split is the whole design of this page.

```sh
./kitchen build boot-matrix
./ci/tier-c.sh
```

Measured on a KVM host: **37 seconds** for all four paths — 5.5 s each for the three
bootloader boots and 4 s for each half of the persistence pair, plus the ISO reads
between them. Under TCG the same four are minutes, which is why CI runs them weekly.

Everything here runs on **any KVM-capable Linux host** with `qemu-system-x86_64`,
`qemu-img`, `xorriso`, `e2fsprogs` and OVMF. Nothing is specific to a particular machine,
and nothing about the machine ends up in the repository.

## The four paths, and what each one proves

| path | how | what only this one catches |
|---|---|---|
| `bios` | isolinux, via SeaBIOS | the BIOS boot record and the isolinux config actually load a kernel |
| `uefi` | GRUB, under OVMF | the EFI System Partition, `BOOTX64.EFI`, and the GRUB menu generated from `isolinux.cfg` |
| `usb` | the ISO attached as a `usb-storage` device | the isohybrid MBR — this is byte-for-byte what a `dd`'d stick is |
| `persistence` | two direct-kernel boots on one writable disk | that the union's writable branch is really on the device and survives a reboot |

All four assert the same three livekit markers, and all four are diffed against the same
golden. That last part is the strongest claim Tier C makes: **every boot path must
assemble an identical filesystem.** A GRUB cmdline that drifts from the isolinux one
shows up here and nowhere else.

## Why the bootloader paths can assert at all

They could not, until two things were fixed.

**The menu entry.** A bootloader's default entry sets no `console=ttyS0`, so its serial
log is empty and the only thing a test can check is that a screenshot file is non-zero —
which is what these boots did for four CI runs while reporting success.
[`serial-console`](../50-cookbook/serial-console.md) adds an entry that does set it, and
`kitchen test` points the bootloader at that entry by reading the ISO's own menu.

Not by counting arrow presses. That was the obvious approach and it is wrong twice over:
Slax's isolinux config sets `MENU ROWS 5`, so the appended serial entry is not on screen,
and two entries carry `MENU DISABLED`, so navigation skips them. Six `LABEL`s, four
selectable, serial three presses away rather than the five a count suggests. And the GRUB
menu is a different list — `uefi-bootable` omits the disabled entries, so a shared count
would select the memory tester under UEFI.

What works is simpler. **Esc** reveals Slax's hidden menu; **Esc again** drops to the
`boot:` prompt, where an entry is typed *by name*. Order-independent, `MENU ROWS`
-independent, and if the name is wrong nothing boots and the serial log stays empty —
which already fails. GRUB has no such prompt, but its menu has neither problem, so there
the entry is counted.

**The console order.** `serial-console` used to end `console=ttyS0 console=tty0`.
`/dev/console` is the *last* `console=`, so userspace wrote to the screen and the serial
port carried kernel messages only: 21 KB of log and zero livekit markers. Reversing them
is what makes the entry do what its name says. The measurement is on
[that recipe's page](../50-cookbook/serial-console.md).

## Persistence is a kernel boot, not a menu boot

```sh
./kitchen test out/slax-boot-matrix-debian-64bit-12.2.0.iso --persistence
```

Two boots, one disk, and a marker written by the first that must be there for the second.
The marker is [`testkit`](../50-cookbook/testkit.md)'s `marker` variable.

Direct-kernel mode rather than the bootloader, deliberately. perch is a livekit-init
behaviour and the bootloader has nothing to do with it, while a menu boot would add
keystroke timing to a test that is already two boots long. It is also the only way to put
`perchdir=` on the cmdline without editing the image under test.

The disk is a 256 MB sparse file with ext4 on it, made with `mkfs.ext4 -F` — no loop
device and no root. ext4 rather than FAT because livekitlib picks its storage strategy
with a live POSIX test: a filesystem that passes gets a plain `mount --bind` with no size
limit, while FAT32 falls back to DynFileFS and a 16 GB XFS container. The bind path is the
one worth testing and it needs no room.

A `dd`'d hybrid image **cannot** be the persistence target — it carries ISO9660 and is
read-only. That is why this attaches a second device rather than writing to the stick.

## What lands in the repository, and what does not

Two files, both facts about the artifact:

| | |
|---|---|
| `tests/boot/tier-c.json` | one row per boot: path, image name and size, markers seen, duration, result. [`ci/release-notes.sh`](ci.md#releases) reads it, so a release's Tier C claim is derived rather than asserted |
| `tests/boot/golden/<profile>-<target>.testkit` | the testkit block the image produces. One per **image**, not per path |

Nothing about the machine goes in either, and that is gated rather than trusted:
`ci/checks/97-tier-c-ledger.sh` validates the ledger against a closed key set and rejects
any string that looks like a path or a home directory. Add a hostname field and it fails.

The ledger also refuses to be written without a commit. The first run recorded
`"commit": "unknown"` because git will not read a repository owned by another user
("dubious ownership") — and said nothing. A ledger that cannot name the tree it tested is
not evidence, so that is now fatal, with the `git config --global --add safe.directory`
line printed for you.

## Cleanup

The persistence disk is the one artifact whose purpose is to **survive** a boot, so it
cannot be deleted on exit and the harness does not own it: `qemu_boot.py --disk` creates
it if absent and never removes it, while `ci/tier-c.sh` scopes it to the run directory and
tears it down afterwards. A failed run keeps it and says where it is — it is the evidence.

Everything else is removed, including two things that used to leak: the QMP socket
directory (27 stale `/tmp/qb-*` had accumulated in one container, 7 on a build host) and
the 540 KB OVMF variables copy each UEFI run wrote into the evidence directory.

**And the cleanup can fail.** `ci/tier-c.sh` counts `/tmp/qb-*` and its own scratch before
and after, and exits non-zero if the run leaked. A cleanup nobody checks is the same
defect as a check that cannot fail.

There is a `trap`, which matters more than it looks: this is normally driven over ssh, and
a dropped connection would otherwise leave a live QEMU behind. It kills only pids this run
started — never `pkill qemu`, which on a shared machine takes out somebody else's work.

## What CI does with all this

The weekly job builds `boot-matrix` and runs the same four paths under TCG. It cannot
produce the ledger, so it writes one to a scratch path and uploads it as an artifact. What
it *does* own is the golden diff: if a recipe change alters the assembled filesystem, the
weekly run goes red against the committed block.

It is off the per-push path. Four boots is four boots, and the last trimming pass took
this job from eight minutes to one by not paying for boots nobody reads.

## What Tier C still does not claim

**Not `runtime-verified`.** Tier C asserts that each path reaches `Live Kit done` and
assembles the right filesystem. Whether Fluxbox came up, the browser launches or the fonts
are readable needs a person and a window — [QEMU by hand](qemu.md).

**Not real hardware.** QEMU says the image boots. It says nothing about the firmware, GPU
or wireless chip in the machine you care about.

**Not Secure Boot.** OVMF ships `secboot` and Microsoft-keyed variants, so the negative
proof is reachable; MOK enrolment needs a physically present human and is a documented
won't-do.
