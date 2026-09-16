# `tools/qemu/` — boot your ISO and look at it

Two scripts for booting a Slax ISO under QEMU **interactively**, so a person can watch the
desktop come up and click things.

```sh
tools/qemu/boot-bios.sh out/slax-custom.iso          # works on any Slax ISO
tools/qemu/boot-uefi.sh out/slax-custom.iso          # needs the uefi-bootable recipe
```

Both default to VNC and print the `ssh -L` command to reach it — on these machines QEMU is
built without GTK and SDL, so VNC is the only way to see a framebuffer at all.

**This is not `tests/`.** Everything under `tests/` is headless and assertion-driven: it
proves an image did not regress. These prove nothing. They let you look.

Full documentation, including installing Slax to a virtual disk:
[`docs/60-testing/qemu.md`](../../docs/60-testing/qemu.md).
