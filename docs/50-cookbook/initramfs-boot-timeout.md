# `initramfs-boot-timeout` — change how long early boot waits

**Status: verified** — applied, `sh -n` clean, boot-verified to `slax login:`.

```sh
kitchen apply initramfs-boot-timeout
```

Also the reference example for the [`initramfs.patch`](#the-verb) verb, which is the most dangerous
one in the toolkit.

## What it changes

`/init` line 36:

```sh
DATA="$(find_data 45 "$DATAMNT")"        ->    find_data 90 "$DATAMNT"
```

`find_data` retries for that many seconds, calling `refresh_devs` once a second, then gives up with
`Could not locate slax data`. It is the single most useful number in early boot to be able to change:

| | |
|---|---|
| **raise it** | slow USB enumeration, spun-down disks, a hub that settles late, a big RAID controller |
| **lower it** | CI and VMs, where 45 s of retrying only delays a failure you already know about |

```yaml
vars:
  seconds: "90"
```

## The verb

`initramfs.patch` edits the four scripts that *are* the boot — `init`, `lib/livekitlib`, `shutdown`,
`lib/config`. A stray character in `livekitlib` and the machine stops somewhere in early init with no
message, because the thing that would print one is the file you just broke.

So it is deliberately strict rather than general. **No unified diffs, no line numbers, no regex** —
an exact string that must appear an exact number of times.

```yaml
- verb: initramfs.patch
  edits:
    - file: /init
      expect_sha256: "1b04a359f56f06fb542b626fdb4796893f80cbfe02fcf90cd69970a3ab3a1153"
      find: 'find_data 45 "$DATAMNT"'
      replace: 'find_data 90 "$DATAMNT"'
      count: 1          # default
```

### Four ways it refuses

Every one of these was exercised, and in each case **the image is left untouched**.

**The file is not what the recipe was written for:**

```
init is sha256 1b04a359f56f06fb..., recipe expects 0000000000000000....
Refusing -- this patch was written for a different build.
```

`expect_sha256` is optional but worth setting. `/init` is byte-identical across all four 12.2.0 and
15.0.4 targets, so one hash covers everything.

**Upstream drifted and the string is gone:**

```
init: expected 1 occurrence(s) of 'find_data 999', found 0. Refusing rather than guessing.
```

This is the failure mode the verb exists to prevent — a patch that silently stops applying, and
nobody notices for three releases.

**The string is ambiguous:**

```
init: expected 1 occurrence(s) of 'debug_shell', found 6. Refusing rather than guessing.
```

Set `count:` if you genuinely mean all of them.

**The result would not boot** — the one that matters most:

```
lib/livekitlib no longer parses as sh after patching:
  /tmp/.../lib/livekitlib: 247: Syntax error: word unexpected (expecting ")")
Refusing to build an initramfs that cannot boot.
```

Every patched file with a `#!...sh` shebang is checked with `sh -n` (or `bash -n`). All four parse
clean as shipped, so this is a reliable gate, and it is the difference between *refuses to ship* and
*bricks the boot*.

## Other things worth patching

| in | to |
|---|---|
| `init` | add markers, or move a `debug_shell` |
| `livekitlib` `persistent_changes` | change the 16000 MB perch floor, or the 4000 MB split |
| `livekitlib` `init_zram` | change the 512 MB zram size |
| `livekitlib` `df … cut -d " " -f 1` | harden to `df -P` — a real latent bug for device names over 20 characters |
| `livekitlib` `modprobe_everything` | stop skipping network drivers, for a machine that boots over the network |

Read [`livekitlib-reference`](../15-upstream/livekitlib-reference.md) first; it lists all 49
functions with line numbers.

## Verify, every time

```sh
kitchen pack && kitchen test out/*.iso --kernel --seconds 240
```

`sh -n` catches syntax, not semantics. A patch can parse perfectly and still hang the boot — only
booting it tells you. `--kernel` puts the whole of livekit init on the serial port, so a failure
names the stage it stopped at.

**On a flaky failure:** the harness retries once if the guest panicked in early kernel init *without
reaching livekit at all*, because TCG is not reliably deterministic — a run of this very recipe
panicked in `mask_ioapic_irq` during `x86_late_time_init` and the identical ISO booted fine
immediately after. A boot that reached livekit and then failed is never retried, so the retry cannot
hide a real bug.

## Limits

- **Not a diff tool.** Porting a large change across a Slax release means rewriting the `find`
  strings, on purpose — there is no fuzzy matching to drift.
- **Needs `CAP_MKNOD`**, like every initramfs verb: the archive holds seven device nodes, and a
  non-root `cpio` turns them into empty files. Preflight refuses before anything is unpacked.
