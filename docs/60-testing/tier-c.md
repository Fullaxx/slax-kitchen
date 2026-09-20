# Tier C — every boot path, on a machine that can boot

Tier A parses the built ISO. Tier B boots the kernel directly and asserts that livekit
init ran. **Tier C boots the image the way a machine would** — through a bootloader, off a
USB device, and twice onto the same disk to prove persistence.

It needs `/dev/kvm`, which GitHub-hosted runners do not have, so CI can exercise the
harness but can never be the evidence. That split is the whole design of this page.

```sh
./kitchen build boot-matrix                       # or --base <target>
./ci/tier-c.sh
```

All four targets, which is one loop because a profile's base is now overridable:

```sh
for t in debian-64bit-12.2.0 debian-32bit-12.2.0 \
         slackware-64bit-15.0.4 slackware-32bit-15.0.4; do
    ./kitchen build boot-matrix --base "$t" --no-test --force
    ./ci/tier-c.sh --target "$t" --iso "out/slax-boot-matrix-$t.iso"
done
```

The ledger **merges by target**, so four invocations accumulate into one document; a
re-run replaces that target's rows rather than appending to them.

## A recorded run needs a clean tree

`ci/tier-c.sh` refuses to start if `git describe --always --dirty` reports a modified
tree, because the stamp it writes into the ledger is read from the **working tree**, and
read at the moment the ledger is *written* — so a tracked file edited while a sweep is in
flight taints every target that finishes afterwards, however unrelated the file. Nothing
downstream catches that: the ledger gate accepts a `-dirty` stamp, so the evidence stops
being reconstructible without anything turning red. Refusing costs a second. Noticing
afterwards costs a re-run, which is how this guard came to exist.

`--allow-dirty` is the exploratory escape hatch, and it must be paired with `--ledger`
and `--golden-dir` pointing somewhere else. On its own it would re-open the same hole
from the other side: the merge is by target, so a single throwaway TCG boot **replaces**
that target's real rows and drags the document's `accel` down with it.

Note what the stamp does *not* say. `ci/tier-c.sh` never builds — it refuses if the image
is absent — so the commit names the tree when the ledger was written, not the tree the
ISO was built from, and the ledger identifies the image by name and size with no hash.
For evidence produced and consumed on one machine that is sufficient. It stops being
sufficient at the first release tag, when the notes describe an artifact to somebody else.

Measured, both ways, on the same image:

| | KVM host | unaccelerated container, TCG |
|---|---|---|
| all four paths, wall clock | **45 s** | **128 s** |
| time to the livekit markers, per boot | 4.0–5.5 s | 21–22 s |

The per-boot figures are time to the markers *after* the menu keystrokes; the UEFI path
additionally waits 10 s before touching the menu, for reasons measured below. The TCG
column is the shape the weekly CI run has, and every boot in it matched the golden
generated under KVM — which is what a golden about an artifact ought to do.

Everything here runs on **any KVM-capable Linux host** with `qemu-system-x86_64`,
`qemu-img`, `xorriso`, `e2fsprogs` and OVMF. Nothing is specific to a particular machine,
and nothing about the machine ends up in the repository.

**That host need not be the one with the checkout.** With a
[boot host](boot-host.md) configured, `ci/tier-c.sh` sends each boot there and the
evidence comes back here — a full four-path sweep measured at 52 s from a container with
no qemu at all. The rows still record what each boot actually got, because
`qemu_boot.py` writes `accel` and `qemu` first-hand on the machine that booted; the
banner says `accel  on <host>` rather than reading a `/dev/kvm` that has nothing to do
with where the boots happen. `--local` ignores the file and boots here.

If the boot host cannot be used, the sweep **stops without writing a ledger** — exit 2
for a configuration it will not act on, 3 for a host it could not reach. A row is a claim
about a boot that happened, and no boot happened.

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

**The timing is a measurement, not a guess, and it caught a bug that would only ever have
appeared weekly.** isolinux draws its menu about a second in under either accelerator, so
a short lead is fine. OVMF does not: under TCG it spends about **nine seconds** in
firmware before GRUB draws anything, and GRUB's five-second default window had opened and
shut by fourteen. A two-second lead worked on the KVM host and would have failed every CI
run. So `uefi-bootable` gained a `menu_timeout` variable, `boot-matrix` asks for 30
seconds of it, and the harness waits 10 — one lead that is correct under both. Verified
under TCG in an unaccelerated container: 22 s to all three markers.

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

## What the four-target sweep found

20 boots, 4 targets, **18 green**. The two that are not are the point of having run it:

| | bios | uefi | usb | persistence |
|---|---|---|---|---|
| `debian-64bit-12.2.0` | ok | ok | ok | ok |
| `debian-32bit-12.2.0` | ok | ok | ok | ok |
| `slackware-64bit-15.0.4` | ok | ok | ok | **FAIL** |
| `slackware-32bit-15.0.4` | ok | ok | ok | **FAIL** |

**296 s wall clock for all four**, measured, with KVM and a 60 s ceiling: about 45 s for a
target whose four paths all pass, and about 100 s for one where persistence boot 2 burns
the ceiling. The hang is the expensive case, which is the right way round.

**Persistence boot 2 wedges on both Slackware targets and passes on both Debian ones** —
[issue #15](https://github.com/Fullaxx/slax-kitchen/issues/15). Boot 1 mounts the device,
creates session #1 and writes the marker everywhere; on Slackware boot 2 stops at
`* Waiting for persistent changes on /dev/sda ...` after three retry dots and never
continues. The kept disk rules out the easy explanations: it is 87% empty, structurally
identical to Debian's, and `dumpe2fs` says it was mounted twice — so the mount succeeded
and something after it hung. The two initramfs userlands are byte-identical outside
`lib/modules/`, so "Slackware is different" is not an explanation either.

Two results that were open questions before the sweep and are not now:

- **`uefi-bootable` works on 32-bit.** It ships `arch: [32bit, 64bit]` and a comment that
  read as if it did not. A 64-bit UEFI machine boots the 32-bit ISO: OVMF loads
  `BOOTX64.EFI`, GRUB boots the i686 kernel. A 32-bit UEFI *machine* is still not covered
  — there is no `bootia32.efi` — and that is a statement about firmware.
- **The goldens differ per flavour, correctly.** Slackware carries `06-devel.sb`, no dpkg
  database, `/etc/rc.d/rc.local`, and a different `.xinitrc`. One golden per target is
  what makes each of those a pinned fact rather than noise.

## What lands in the repository, and what does not

Two files, both facts about the artifact:

| | |
|---|---|
| `tests/boot/tier-c.json` | one row per boot: path, image name and size, markers seen, duration, result, and the accelerator and qemu version that booted it. [`ci/release-notes.sh`](ci.md#releases) reads it, so a release's Tier C claim is derived rather than asserted |
| `tests/boot/golden/<profile>-<target>.testkit` | the testkit block the image produces. One per **image**, not per path |

Nothing about the machine goes in either, and that is gated rather than trusted:
`ci/checks/97-tier-c-ledger.sh` validates the ledger against a closed key set and rejects
any string containing a path separator: every field is a name, a version, a marker or one of
a fixed set. Add a hostname field and it fails.

The ledger also refuses to be written without a commit. The first run recorded
`"commit": "unknown"` because git will not read a repository owned by another user
("dubious ownership") — and said nothing. A ledger that cannot name the tree it tested is
not evidence, so that is now fatal, with the `git config --global --add safe.directory`
line printed for you.

**The accelerator and the qemu version come from the rows.** `qemu_boot.py` records both beside
the qemu it ran, because it is the only process that knows them first-hand.
- They used to be measured by `ci/tier-c.sh` on its own machine. That was right only while
  that machine was the one that booted, and gave "unknown" for qemu when it had none, which the
  gate let through.
- The top-level `accel` is `kvm` only if every row of the run is.
- A run whose rows don't name one qemu version writes no ledger at all.
- The gate refuses "unknown" wherever a qemu is recorded.

**The release claim reads the rows too.** The ledger merges by target, so its top-level fields
describe only the last run. The claim used to print those as if they covered every target: two
targets booted days apart, at two commits and on two qemus, came out as one sentence with the
last run's commit and version. It now groups targets by what their own rows say, and states one
sentence per group. Rows written before rows carried these fields fall back to the top level,
which is why the claim about the ledger committed today reads exactly as it did.

## Cleanup

The persistence disk is the one artifact whose purpose is to **survive** a boot, so it
cannot be deleted on exit and the harness does not own it: `qemu_boot.py --disk` creates
it if absent and never removes it, while `ci/tier-c.sh` scopes it to the run directory and
tears it down afterwards. A failed run keeps it and says where it is — it is the evidence.

Everything else is removed, including two things that used to leak: the QMP socket
directory (27 stale `/tmp/qb-*` had accumulated in one container, 7 on a build host) and
the 540 KB OVMF variables copy each UEFI run wrote into the evidence directory.

**And the cleanup can fail.** `ci/tier-c.sh` counts `qb-*` and its own scratch before
and after, and exits non-zero if the run leaked. A cleanup nobody checks is the same
defect as a check that cannot fail.

It counts where the harness puts them. That is Python's temporary directory, so `$TMPDIR` if it
is set, not `/tmp` whatever happens. It used to count `/tmp/qb-*`, so with `TMPDIR` set anywhere
else it compared two counts of nothing and printed "clean" over a real leak.
[`tests/unit/test_tier_c_run.py`](../../tests/unit/test_tier_c_run.py) leaks one there on
purpose and requires the run to fail.

**A guest nobody stopped fails the run, by name.** `qemu_boot.py` removes its own pid file
on the way out, so one still sitting in `$OUT/pids` when a boot path returns means that
boot lost its guest. Each path answers for its own leftovers before the next one starts.

That used to be the `trap`'s job alone, and the trap runs at `EXIT` — after `exit $rc` has
already fixed the status. So an orphaned guest was killed in silence and the sweep reported
success, which is the same defect as a check that cannot fail.
[`lib/boot_host.py`](../../lib/boot_host.py)'s agent has always named its leftovers and
failed the run; this is the local half catching up.

**And a pid is checked before it is signalled.** "Only pids this run started" used to mean
"whatever number is in the file", killed at exit — up to minutes after the boot that wrote
it, and a pid is exactly what a busy machine recycles. So the promise never to take out
somebody else's virtual machines was not kept. `/proc/<pid>/cmdline` must now name both
qemu and this run's output directory before anything is signalled; anything else is
reported and left alone, which is the safe direction to be wrong in.
[`tests/unit/test_tier_c_run.py`](../../tests/unit/test_tier_c_run.py) plants both kinds and
requires the foreign one to still be running afterwards.

## What CI does with all this

The weekly job builds `boot-matrix` and runs the same four paths under TCG. It cannot
produce the ledger, so it writes one to a scratch path and uploads it as an artifact. What
it *does* own is the golden diff: if a recipe change alters the assembled filesystem, the
weekly run goes red against the committed block.

Measured on a GitHub runner: **2 m 07 s** for the build and all five boots, inside a boot
job of 3 m 21 s. That job was eight minutes before this work, so it now does four more
boots in less than half the time — the screenshot modes it replaced spent 240 seconds
asleep and could only fail on a zero-byte PNG.

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
