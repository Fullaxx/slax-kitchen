# 20 · The boot sequence

Firmware to desktop, once per medium. The chain diverges only at the front — from the moment the
kernel starts, all five routes are identical.

| | |
|---|---|
| [`overview.md`](overview.md) | the whole chain in one diagram, and where the five routes converge |
| [`bios-cd.md`](bios-cd.md) | El Torito → `isolinux.bin`. The only route a stock ISO supports |
| [`bios-usb-hdd.md`](bios-usb-hdd.md) | MBR → VBR → `ldlinux.sys`. What `bootinst` builds |
| [`uefi-usb-hdd.md`](uefi-usb-hdd.md) | ESP → `BOOTX64.EFI`. Works only after `bootinst` moves the files |
| [`uefi-cd-gap.md`](uefi-cd-gap.md) | why no UEFI machine boots a stock Slax ISO, and what fixes it |
| [`pxe-and-http.md`](pxe-and-http.md) | `pxelinux.0`, `ip=`, and `from=http://…iso` over httpfs2 |
| [`secure-boot.md`](secure-boot.md) | why Slax cannot boot with Secure Boot on, and what the manual route costs |
| [`boot-parameters.md`](boot-parameters.md) | every parameter, from grepping the consumers rather than the docs |

## Which page you want

| | |
|---|---|
| "it boots from CD but not from my USB stick" | [`bios-usb-hdd.md`](bios-usb-hdd.md) — you probably `dd`'d it |
| "my laptop doesn't see the ISO at all" | [`uefi-cd-gap.md`](uefi-cd-gap.md) |
| "what can I put on the `APPEND` line?" | [`secure-boot.md`](secure-boot.md) | why Slax cannot boot with Secure Boot on, and what the manual route costs |
| [`boot-parameters.md`](boot-parameters.md) |
| "where does it stop when it fails?" | [`overview.md`](overview.md), then `debug` |
| "what happens after `/init`?" | [`10-anatomy/livekit-init.md`](../10-anatomy/livekit-init.md) |
