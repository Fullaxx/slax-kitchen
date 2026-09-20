# The boot host — running boot tests on a machine with KVM

Every boot test in this project runs qemu where the checkout is. That is the wrong place
often enough to be worth fixing: the container this is usually developed in has no
`/dev/kvm` and no qemu at all, so `kitchen test --kernel` either runs ten to twenty times
slower under TCG or cannot run. The machine with the KVM is frequently not the machine
with the working tree.

A **boot host** closes that gap. One small file says "use that machine", and the boot runs
there with the evidence landing here, in the same place, under the same names.

```console
$ ./kitchen test out/slax-boot-matrix-debian-64bit-12.2.0.iso --kernel
  slax-boot-matrix-debian-64bit-12.2.0.iso -> kvmbox  (kvmbox, qemu 8.2.2, KVM)
  sent 301 files (image already cached), 1.2s
* kernel boot  (direct, bypasses the bootloader)
boot kernel: slax-boot-matrix-debian-64bit-12.2.0.iso   (KVM)
  waited     : 4s of a 60s ceiling (all expectations seen)
  serial log : out/boot-tests/slax-boot-matrix-debian-64bit-12.2.0-kernel.serial.log
  ok   serial contains 'Looking for slax data'
  ok   serial contains 'Mounting bundles'
  ok   serial contains 'Live Kit done, starting slax'
  evidence in out/boot-tests
```

Nothing else changes and nothing else knows: [`kitchen test`](../90-reference/cli.md) is
the one funnel every boot goes through, so `kitchen build`'s profile tests and
[`ci/tier-c.sh`](tier-c.md) follow it there without a line of their own, and so does
[the interactive launcher](qemu.md), which brings its VNC window back over an ssh tunnel.
The exception is `--structure`, which reads the image rather than booting it and stays
here.

## Setting one up

```sh
cp boot-host.example.ini boot-host.ini
chmod 600 boot-host.ini
$EDITOR boot-host.ini
./kitchen boot-host check
```

`boot-host.ini` is **gitignored, and refused by the commit gates**
([`ci/checks/10-no-dnc.sh`](../../ci/checks/10-no-dnc.sh)). It names your machine and your
account, which is nobody else's business — and it is not merely private, it is the file
that decides which machine an image is sent to and booted on. A fork that committed one
would point every cloner's boot tests at the fork author's machine, and the clone would
work, so nobody would look. `lib/boot_host.py` refuses to use a tracked one for the same
reason.

```ini
[boot-host]
host = <kvm-host>
scratch = <absolute path on that host>
;vnc = 127.0.0.1:5900
;vnc_public = no
```

| key | |
|---|---|
| `host` | **required.** A hostname, an IP, or an alias from your `~/.ssh/config` — which is where a username, port or identity file belongs, because ssh already reads it. |
| `scratch` | **required.** An absolute path on that machine this account can write. Everything lives under `<scratch>/boot-host/`; nothing outside it is touched. |
| `vnc` | `IP[:PORT]` the interactive launcher's VNC server binds there. Default `127.0.0.1:5900`, reached through an ssh tunnel. An IP literal, never a name; IPv6 must be bracketed (`[::1]:5900`). The port is a port, not a display number, and cannot be below 5900 — qemu listens on `5900 + display`. |
| `vnc_public` | `yes` to confirm a `vnc` outside the private ranges. Required, because that server has no password. |

[`boot-host.example.ini`](../../boot-host.example.ini) is the committed template and names
nothing real.

**Key-based login only.** ssh runs with `BatchMode=yes` everywhere, so it never prompts,
and there is nowhere in the file to put a username or a password. That is deliberate: a
prompt in a build script is a command that hangs waiting for a terminal nobody is watching.
Set the key up first — `ssh-copy-id <host>`, then `ssh <host> true`.

**On the boot host** you need `qemu-system-x86_64`, `qemu-img`, `xorriso`, `e2fsprogs`,
`rsync`, `python3` ≥ 3.9, a writable `/dev/kvm`, and `ovmf` for `--uefi`. Nothing else, and
no pip: the agent that runs there is one stdlib Python file. `kitchen boot-host check`
reports whatever is missing and names the fix.

```console
$ ./kitchen boot-host check
boot host  kvmbox
  workdir  /srv/scratch/boot-host  (everything this writes lives here)
  vnc      127.0.0.1:5900  (tunnelled through ssh)
  host     kvmbox, Ubuntu 24.04.4 LTS
  ok   /srv/scratch/boot-host is yours and private
  ok   tools: qemu-system-x86_64, qemu-img, xorriso, mkfs.ext4, rsync
  ok   python3 3.12.3
  ok   qemu-system-x86_64 8.2.2
  ok   /dev/kvm is writable by this account
  ok   OVMF firmware for --uefi
  ok   73.7 GiB free under /srv/scratch
  0 run(s) kept, cache 0 MiB in 0 image(s)

ready  boots run on kvmbox
```

## Booting here instead

`KITCHEN_BOOT_HOST=local` wins over the file, for one command or for a shell:

```sh
KITCHEN_BOOT_HOST=local ./kitchen test out/x.iso --kernel
./kitchen test out/x.iso --kernel --local     # the same thing, per command
./kitchen build example --local
ci/tier-c.sh --local
```

**A configured host that cannot be used fails the command.** It never quietly falls back
to a local boot. The reason to configure one is that booting here is slow or impossible,
so a silent fallback would turn "a broken ssh key" into the symptom "my tests got slower",
which is a bad trade for anyone trying to produce evidence.

## What actually happens

Everything lives under one directory, created `0700`:

```
<scratch>/boot-host/
  .slax-kitchen-boot-host    marker
  agent/<sha12>/             the agent, named by its own content
  cache/<sha256>/<name>      images, keyed on content
  disks/                     the launcher's relative --disk paths; clean never touches it
  runs/<id>/                 one per run, flock'd for its life
      tree/  <name>  golden/  out/  tmp/  runs.jsonl
```

1. **The agent goes over first**, by rsync, into a directory named after its sha256. The
   boot host then verifies that digest itself, immediately before running it: rsync exiting
   0 is a claim about a transfer, `sha256sum -c` is a claim about the bytes about to run.
2. **One ssh connection stays open** for the session, carrying JSON lines both ways. Its
   stdin is how the boot host learns the driver is still there.
3. **A pre-run check** on the host: the tools, `/dev/kvm`, OVMF if `--uefi` was asked for,
   free space, and whether a unix socket path still fits under your scratch directory.
4. **A stale sweep** removes run directories nobody holds a lock on, killing only processes
   whose `/proc/<pid>/cmdline` names that run — never `pkill qemu`, because a boot host is
   somebody's machine and taking out their other virtual machines is far worse than a
   leaked guest.
5. **The tree goes over** as `git ls-files --cached --others --exclude-standard`: exactly
   what a local run reads, which skips `out/`, `work/` and `isos/` — and, because it is
   gitignored, can never include `boot-host.ini` itself.
6. **The image goes over once per content.** Keyed on sha256, not on name: two `kitchen
   build` runs produce the same filename with different bytes, and a name-keyed cache would
   boot the first one forever. It arrives as `.partial`, is verified on the host, and only
   then gets its real name.
7. **The boot runs** under `boot.lock`, one at a time. Menu modes measure keystrokes against
   a bootloader countdown, so two boots sharing a CPU do not merely go slower — they miss
   the menu and assert against an entry nobody selected.
8. **Leftovers are named, not counted.** `TMPDIR` is the run's own directory, so anything
   still in it afterwards belongs to this boot and fails it by name. A pid file still
   present means an orphaned guest: it is killed and the run fails.
9. **The evidence comes back**, `pids/` excluded and never with `--delete`. A screenshot
   from an older local run is deleted first if this boot produced none — `qemu_boot.py`
   unlinks its own outputs, but it cannot reach across a machine boundary, and a stale PNG
   beside a fresh serial log reads as evidence of a boot that never produced it.

### When the driver goes away

The boot host does not keep a guest alive for a driver that has gone. The connection's
stdin is the signal, and there are two of them: EOF, and a heartbeat every five seconds
whose absence for thirty seconds means the same thing. Either one sends `SIGTERM` to the
run's whole process group — qemu is in it — then `SIGKILL` ten seconds later, and removes
the run directory.

Measured, with a real guest up on the boot host:

| what was done here | what happened there |
|---|---|
| Ctrl-C | guest killed, run directory removed, agent exited |
| driver `SIGKILL`ed | same, on EOF |
| driver suspended (connection open, heartbeat stopped) | alive at 20 s, killed and removed by 35 s |

A run directory is **kept** in exactly one case: the copy-back failed, so the boot host
holds the only copy of the evidence. Kept runs expire after seven days, cached images after
fourteen.

## Commands

```sh
./kitchen boot-host check     # can it run the tests? names anything missing
./kitchen boot-host clean     # remove cached images, finished runs and old agents
./kitchen boot-host show      # what the file says, without connecting
```

`clean` never touches `disks/`, which holds work somebody asked for rather than this
tool's bookkeeping.

## Exit codes

| | |
|---|---|
| 0, 1 | the boot test's own, unchanged — a caller cannot tell a remote run from a local one |
| 2 | the configuration was refused, before anything connected |
| 3 | it could not be run: unreachable, the pre-run check failed, or the session was lost |

On 2 or 3 nothing ran, and `ci/tier-c.sh` stops on either **without writing a ledger**,
because a ledger row is a claim about a boot that happened. `kitchen build` says the ISO
could not be tested, rather than that its tests failed — those are different states.

## What it costs

Measured against a 422 MiB image, from a container with no qemu at all to a KVM host on
the same network:

| | |
|---|---|
| `kitchen boot-host check` | 0.7 s |
| `--kernel`, image not yet cached | 7.3 s total, of which 2.6 s is sending 301 files and the image |
| `--kernel`, image cached | 5.9 s total, of which 1.2 s is sending the tree |
| the boot itself | 4–5 s under KVM |
| the same test locally | impossible here — no qemu is installed in this container |

So the per-call overhead is around a second and a half once an image has been sent, which
is the interesting number: a Tier C sweep sends the image once and then pays it per boot.

## When something is wrong

Every refusal names what to go and do. The ones worth knowing in advance:

| message | what it means |
|---|---|
| `<host> refused the key` | key-based login is not set up. `ssh-copy-id <host>`, then `ssh <host> true`. |
| `<host> accepted the login, but this account cannot create <dir>` | `scratch` points somewhere this account cannot write. Not a key problem — the login worked; that is how the `mkdir` got far enough to fail. |
| `<user> cannot write /dev/kvm (owned by group kvm)` | `sudo usermod -aG kvm <user>`, then log in again — and `ssh -O exit <host>` first if you use `ControlMaster`, or the old session is reused with the old groups. |
| `is in group kvm ... but this session was not granted it` | the group was added after the session started. Same fix, without the `usermod`. |
| `scratch is N characters and the longest that works is M` | qemu's QMP socket lives under it and a unix socket path stops at 108 bytes. Choose a shorter path. |
| `vnc = <addr> is NOT a private address` | that server has no password. Add `vnc_public = yes` if you mean it. |
| `write an IPv6 address in brackets` | `fe80::1:5900` is both "`fe80::1` port 5900" and a valid address on its own. Write `[fe80::1]:5900`. |
| `the VNC tunnel cannot be opened: ... already in use` | another launch holds that port; its viewer is on it. Quit that one, or `--vnc-addr <ip>:<port+1>`. |

## What is checked by a gate, and what is not

**Checked on every commit.** `lib/boot_host.py`'s own decisions are unit-tested and need no
host: every configuration refusal, the private-address table, the bracket rule for IPv6, the
ssh argument list (`BatchMode=yes` is one word in one list, and that is what keeps it there),
the agent being verified before it runs, which files the tree transfer sends, and the rewriting
of remote paths back to local ones. `kitchen test`'s handover is covered in
[`test_kitchen_test.py`](../../tests/unit/test_kitchen_test.py) and the sweep's remote mode in
[`test_tier_c_run.py`](../../tests/unit/test_tier_c_run.py).

**Proven by hand against a real host, and deliberately not automated.** These were exercised
against a KVM host on 2026-09-19: a four-path Tier C sweep in 52.7 s with every row reporting
`accel: kvm`; an unreachable host stopping at the first path with no ledger written; teardown
under `SIGTERM`, under `SIGKILL`, and with the driver suspended — the guest still alive at 20 s
of silence and swept by 35 s, which is the heartbeat and watchdog doing their job; the stale
sweep; a TMPDIR leak failing the run by name; and the VNC tunnel, checked by completing an RFB
handshake through to `ServerInit` rather than by opening a socket, because a tunnel accepts a
local connection before it knows whether anything is listening at the far end.

There is no end-to-end test of that, and that is a decision rather than an omission. Driving it
under a fake `ssh` would mostly assert that ssh connects, that rsync transfers and that a viewer
opens a session — none of which is this project's code, and all of which
[have their own tests](../../CONTRIBUTING.md#what-a-test-here-is-for). Running qemu somewhere
else is a convenience for working faster, not a property of the images this repo builds. The
cost of such a harness would be paid on every push by everyone, including everyone who never
configures a boot host at all.

So the gap is stated rather than hidden: **if the transport breaks, a gate will not tell you.**
`kitchen boot-host check` will, in about a second, and it is the first thing to run when a boot
that used to work stops working.

## See also

- [QEMU by hand](qemu.md) — the interactive launcher, which uses the same boot host
- [Tier C](tier-c.md) — the committed boot evidence, and what a ledger row claims
- [CI](ci.md) — what runs on every push, and why it can never be the evidence
- [`lib/boot_host.py`](../../lib/boot_host.py) — the driver and the agent, one stdlib file
