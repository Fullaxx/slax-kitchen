# `tools/qemu/` — boot your ISO and look at it

One launcher for booting a Slax ISO under QEMU **interactively**, so a person can watch the
desktop come up and click things — or for printing the commands, to boot it on another machine.

```sh
tools/qemu/boot.py out/slax-custom.iso --bios                  # any Slax ISO
tools/qemu/boot.py out/slax-custom.iso --uefi                  # needs the uefi-bootable recipe
tools/qemu/boot.py out/slax-custom.iso --uefi --print > run.sh # the same boot, for the KVM host
```

`--bios` or `--uefi` is the one choice you must make. Everything else has a default: x64, the `pc`
machine, 2048 MiB, the ISO as a CD-ROM, and VNC on 127.0.0.1 with the `ssh -L` command printed for
you — on these machines QEMU is built without GTK and SDL, so VNC is the only way to see a
framebuffer at all. Architecture, machine, storage buses, disks, serial and network all have flags;
`--help` lists them.

**This is not `tests/`.** Everything under `tests/` is headless and assertion-driven: it proves an
image did not regress. This proves nothing. It lets you look. Nothing in CI runs it, so
`tests/unit/test_tools_qemu.py` pins the commands it builds.

Full documentation, including buses, persistence disks, direct kernel boot and `--print`:
[`docs/60-testing/qemu.md`](../../docs/60-testing/qemu.md).
