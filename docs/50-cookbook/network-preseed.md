# `network-preseed` — ship a Wi-Fi network

**Status: artifact boot-verified.** Built and structurally asserted on all four
targets; then booted under QEMU with [`testkit`](testkit.md), which confirmed the ConnMan provisioning file reached the union (201 bytes).
**Runtime behaviour is still untested** — that the file is correct is not the same as
the feature working, and the difference needs a full desktop boot.

```sh
kitchen apply network-preseed
```

```yaml
vars:
  ssid: "MyNetwork"
  psk: "example-passphrase"
```

## One file, both flavours

**ConnMan is the network stack on both.** NetworkManager is not installed on either — only a couple
of orphan `/etc/NetworkManager` directories left behind by unrelated packages. So a single
provisioning file covers Debian and Slackware alike.

`/var/lib/connman` is ConnMan's `STORAGEDIR` and ships **empty**. Any `*.config` file there is read
as a provisioning file:

```ini
[global]
Name = slax-kitchen preseed

[service_kitchen_wifi]
Type = wifi
Name = MyNetwork
Passphrase = example-passphrase
Security = psk
IPv4 = dhcp
```

The `[service_*]` section name is arbitrary; the `service_` prefix is not. Other useful keys:
`SSID` (hex, for a non-UTF-8 name), `Hidden`, `Nameservers`, `Domain`, and the `EAP` family for
enterprise networks.

It is written `0600` because it holds a passphrase. The stock image has nothing in that directory
to take a convention from, so that is ours.

## When it actually takes effect differs by flavour

| | |
|---|---|
| **Debian** | `connman.service` is enabled in `multi-user.target`, so ConnMan runs at boot whether or not X starts. A `text` boot still gets the network. |
| **Slackware** | there is **no `rc.connman`**. `connmand` is started as a background job from `/root/.fluxbox/startup` — only inside the X session, only as root. A `text` boot has no ConnMan at all and this file is never read. |

Wired networking is unaffected on Slackware: `rc.inet1` runs `dhcpcd` on every interface at boot,
independently of ConnMan. It is **Wi-Fi** that depends on the desktop having started.

The recipe does not try to fix that. Starting `connmand` at boot means shipping an rc script — the
shape [`enable-ssh`](enable-ssh.md) uses — and doing it silently inside a Wi-Fi recipe would be
surprising.

## A PSK in an image is a published PSK

Anyone who can read the ISO can read the passphrase; a squashfs bundle is not a secret store. That
is a property of preseeding, not of this recipe. If the network matters, use a throwaway SSID for
the image or hand the credentials over some other way.

`ci/checks/50-secrets.sh` blocks real-looking credentials in this repository and provides the
`# kitchen:allow-secret` marker for deliberate examples, which this recipe uses.
