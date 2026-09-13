# Using Slax

How to *use* Slax, as opposed to how to customize it. Accurate for stock Slax 12.2.0 (Debian) and
15.0.4 (Slackware); where slax-kitchen changes a behaviour, the page says so.

| | |
|---|---|
| [quick-start](quick-start.md) | boot it, log in, first five minutes |
| [whats-in-slax](whats-in-slax.md) | what software is actually on the image |
| [install-to-usb](install-to-usb.md) | the three routes, and which one to use |
| [install-to-harddisk](install-to-harddisk.md) | dedicated partition, or alongside an existing bootloader |
| [persistence-perch](persistence-perch.md) | **the one people get wrong** — saving your changes |
| [bundles-at-runtime](bundles-at-runtime.md) | loading and saving software without rebooting |
| [boot-modes-and-tricks](boot-modes-and-tricks.md) | every boot parameter, PXE, booting from an ISO file |
| [everyday-tasks](everyday-tasks.md) | networking, installing software, keyboard, resolution |
| [troubleshooting](troubleshooting.md) | when it does not boot |

## The one paragraph version

Slax runs entirely from read-only squashfs **bundles** stacked into a single filesystem by **aufs**.
Nothing is installed. Everything you change lives in RAM and disappears at shutdown — unless you
boot from writable media with persistence on, or freeze your changes into a bundle with
`savechanges`. Bundle load order is the **numeric prefix**, and **higher wins**.
