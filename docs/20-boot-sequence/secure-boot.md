# Secure Boot

**Slax cannot boot with Secure Boot enabled, and `slax-kitchen` does not ship a recipe that claims
otherwise.** This page explains why, and what the manual route actually costs, so the decision to
turn Secure Boot off is an informed one rather than a shrug.

## The chain, and where Slax falls out of it

```
firmware  ──trusts──▶  shim          signed by Microsoft's UEFI CA
          ──trusts──▶  GRUB          signed by the shim's embedded certificate
          ──trusts──▶  vmlinuz       must be signed by a key in db, or an enrolled MOK
                       ▲
                       └── Slax's kernel is custom-built and UNSIGNED
```

Verified on the shipped image — there is no signature appended to `vmlinuz`:

```sh
tail -c 64 vmlinuz | strings | grep -i 'Module signature'   # nothing
```

The first two links are solvable off the shelf: Debian ships `shim-signed` and
`grub-efi-amd64-signed`. The third is the problem. Slax's kernel is rebuilt from source with aufs
patched in (see [kernel](../30-inventory/kernel.md)), so no distribution has signed it and none will.

## Why there is no recipe

A verb could, in principle, sign `vmlinuz` and lay out shim + GRUB. It would still be the wrong thing
to ship, for three reasons:

**1. We cannot ship a signing key.** A private key in a public repository is a key everyone has.
Anything signed with it is trusted by anyone who enrolled it, which is worse than not signing at all
— it is security theatre that looks like security. The key has to be yours, generated on your
machine, kept off the image.

**2. Enrolment is irreducibly manual.** Your key is not in the firmware's `db`, so it has to be
enrolled as a **MOK** — Machine Owner Key. That means `mokutil --import`, a reboot, and then
MokManager's blue screen, where a **physically present human** types the password and confirms. Per
machine. It cannot be scripted, by design; that is the whole point of the mechanism.

**3. It cannot be verified here.** Every other recipe in this project is boot-tested before it
ships. Secure Boot needs real firmware with it enabled — QEMU + OVMF can approximate it, but the
approximation is exactly where a Secure Boot bug would hide. It is item **H-005** in the host queue
for that reason.

A recipe that produces an image which still will not boot, on a path we cannot test, gated behind a
manual step we cannot perform, is not a feature.

## If you need it anyway

The honest procedure, on your own machine, for a build only you will boot:

```sh
# 1. your own key
openssl req -new -x509 -newkey rsa:2048 -keyout MOK.key -out MOK.crt \
        -nodes -days 3650 -subj "/CN=my slax signing key/"
openssl x509 -in MOK.crt -outform DER -out MOK.cer

# 2. sign the kernel
apt-get install sbsigntool
sbsign --key MOK.key --cert MOK.crt --output vmlinuz.signed work/iso/slax/boot/vmlinuz
sbverify --cert MOK.crt vmlinuz.signed

# 3. replace it in the tree, and rebuild
cp vmlinuz.signed work/iso/slax/boot/vmlinuz
kitchen pack

# 4. on the target machine, once, with a human at the keyboard
mokutil --import MOK.cer      # sets a one-time password
reboot                        # MokManager appears; enrol the key, enter the password
```

You also need shim and a signed GRUB in the ESP rather than `syslinux.efi`, which is not signed by
anyone:

```sh
apt-get install shim-signed grub-efi-amd64-signed
# shimx64.efi.signed -> EFI/BOOT/BOOTX64.EFI
# grubx64.efi.signed -> EFI/BOOT/grubx64.efi
```

**Nothing above is tested by this project.** It is written down because the alternative is leaving
you to reconstruct it, not because it is supported.

## The pragmatic answer

Turn Secure Boot off in firmware to boot Slax, and back on afterwards if you want it for your
installed OS. Slax is a live system you boot deliberately from removable media; the threat model
Secure Boot addresses — persistent bootkits on an installed system — is largely not the one you are
in while running it.

Related: the stock ISO cannot boot on UEFI **at all**, Secure Boot or not, until the
[`uefi-bootable`](../50-cookbook/uefi-bootable.md) recipe is applied. That gap is separate and is
fixed. See [the UEFI gap](uefi-cd-gap.md).
