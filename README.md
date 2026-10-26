# acer-pt516-nix

NixOS support for the Acer Predator PT516-52s, sold as the Predator Triton
500 SE. This is the only supported model. Hardware acceptance was run on
kernel 7.2.7 with BIOS V1.04; kernel 6.18.52 compiles only and has not been
tested on hardware ([verification](docs/verification.md)).

The hardware layer alone provides the physical Turbo toggle, thermal
profiles, fan control and keyboard sysfs interfaces, without the running
broker or session policy. It uses the broker executable only for the
one-shot boot reset. Automatic lighting and battery add-ons need the broker.

To report another model, [open a GitHub issue](https://github.com/Nickdom1/acer-pt516-nix/issues)
with the product name, BIOS version, kernel version, symptoms and reviewed
`nix run .#pt516-check` output. Never include serial numbers, machine IDs or
firmware dumps.

On stock Linux this laptop's Turbo key does nothing. Pressing it is meant
to put the machine in its loudest, fastest state: fans to maximum and a
higher power budget for the CPU and GPU. This repository makes the key
work, and adds two optional pieces for anyone who wants Linux to do
something useful when Turbo turns on.

The hardware fixes are prepared for upstream submission to the Linux
kernel, power-profiles-daemon and nixpkgs. A fix leaves this repository
after it is available in the selected source and the replacement is
verified. Nothing has been submitted yet; [UPSTREAM.md](UPSTREAM.md)
tracks each piece.

## Why the key needed more than a one-line fix

The kernel driver for Acer laptops, `acer-wmi`, already handles Turbo on
several Predator models. It just does not recognize this one. Teaching it
the model (a one-line quirk) turned out not to be enough:

- The firmware answers "is Turbo on?" with a 12-byte reply. The driver
  accepted only 4 or 8 bytes, read the answer as "off", and so every press
  switched Turbo on again with the fans stuck at maximum.
- The firmware expects a different code for the Turbo light than the one
  the driver sent, and the driver never checked the firmware's refusal.
- The key only changed the fan mode. The power budgets that make Turbo
  Turbo come from the thermal profile, which the key never touched.

Eleven small kernel patches fix this. User space had a matching problem:
power-profiles-daemon, the service your desktop uses to show and pick a
power profile, misspelled one of the profile names the kernel reports and
lost track of it, and it could not apply its settings while another
program had changed the CPU governor. Two patches fix that. The longer
story and the credits: [docs/background.md](docs/background.md).

## What is in the box

Three layers. You enable each one separately, and the first works alone.

**The driver** (`pt516.hardware`). Builds the patched driver as its own
kernel module, `acer_wmi_pt516`, against whatever kernel you run, and keeps
the stock `acer_wmi` out of the way. The key becomes an on/off toggle, as
on Windows. Each boot starts calm, on the balanced profile with automatic
fans, even when the firmware remembered Turbo from last time. thermald is
switched on so the firmware's thermal tables apply, and power-profiles-daemon
gets its two patches. This layer never touches your desktop session, your
background services or your VMs.

**The broker** (`pt516.turbo`). A small root service, `turbo-broker`, that
watches the key. When Turbo turns on, it sets the keyboard backlight to a
color of your choice and can keep it lit; when Turbo turns off, it puts back
exactly what was there before. It decides what happens when you unplug the
charger during Turbo (by default it ends Turbo), and it hands games a
performance profile through GameMode without touching the fans. Off by
default.

**The session policy** (`services.performance-policy`). Nothing
Acer-specific: a NixOS module that, while a game or Turbo is active,
protects the game's memory from being swapped out and stops the background
services and VMs you list, then restarts only the ones it stopped. It works
on any machine. Off by default, with empty lists until you fill them.

With the broker and the policy both on, Turbo and games drive the policy.
That link is configuration; enabling one layer never enables another.

## Quick start

A flake is a Nix project with pinned inputs. Add this repository as an
input and import the layers you want:

```nix
# flake.nix of your system; flake.lock pins the resolved revision
inputs.acer-pt516-nix.url = "github:Nickdom1/acer-pt516-nix";

# in nixosSystem { modules = [ ... ]; }
acer-pt516-nix.nixosModules.hardware
{ pt516.hardware.enable = true; }
```

Your system's `flake.lock` pins the resolved revision; review changes when
updating that input. Without flakes, import `modules/hardware` (or
`modules/broker`, `modules/policy`) from a pinned checkout; the modules use
no flake inputs.

Two complete examples: [examples/minimal](examples/minimal/configuration.nix)
(driver only) and [examples/broker-policy](examples/broker-policy/configuration.nix)
(all three layers for one user). Every option and its default:
[docs/configuration.md](docs/configuration.md). How the pieces fit and
which one owns which piece of hardware state:
[docs/architecture.md](docs/architecture.md).

## What it needs from your system

The modules build with your own nixpkgs, your kernel and your
power-profiles-daemon. Importing them never swaps your package set. I run
kernel 7.2.7 with the NVIDIA open driver on nixpkgs 26.05 (the exact
revisions are `lib.tested` in [flake.nix](flake.nix)). The module also
compiles against 26.05's default 6.18.52 kernel, which I have not booted.
Other kernels get an evaluation warning.

The power-profiles-daemon patches are made for release 0.30, the version in
nixpkgs 26.05. Any other version refuses to evaluate until you turn the two
patches off or this repository supports it.

GPU and laptop defaults (PRIME bus IDs, Intel graphics) are not set here.
Use your own, or the nixos-hardware profile once one exists for this model.

Users and groups: the broker's user joins `turbo-broker-status`; the
policy's user joins `gamemode` (or `performance-policy` when GameMode is
off). When both layers are on, both options must name the same user.
Membership applies at the next login.

## Checking it works, and going back

`nix run .#pt516-check` reads the model, the loaded driver, the profile,
the fans, the keyboard zones and the services, and writes nothing. After a
fresh boot the profile is balanced and the fans automatic; one key press
gives the performance profile and Turbo fans; the next press puts it back.
More checks, and how I test a release on the machine:
[docs/verification.md](docs/verification.md).

To roll back, boot the previous NixOS generation. The driver and its
blacklist belong to the generation, so the old driver comes back with it.

## Limits

- One model. Other Acer or Predator machines are not supported, and the
  driver quirk matches this model's DMI name only.
- Suspend and wake, keyboard color across a power-off, and the 6.18.52
  kernel are not tested on hardware.
- `turbo-broker on` and `off` write the profile and fan attributes directly;
  they do not update the key's own state inside the driver. Use the physical
  key to judge key behavior.
- Only NixOS is tested. [docs/non-nixos.md](docs/non-nixos.md) shows what
  the build does so you can reproduce it by hand; there is no DKMS package
  or installer.

## Upstream status

None of the eleven kernel patches (for platform-drivers-x86), the two
power-profiles-daemon patches or the thermald unit change (nixpkgs) has
been submitted yet. [UPSTREAM.md](UPSTREAM.md) is the status table, with
source revisions and the rule for when a patch leaves this repository
([maintainers/README.md](maintainers/README.md)).

## License

The Nix modules, the Python programs, the tests, the maintainer tools and
the documentation are under the MIT license ([LICENSE](LICENSE)).
The vendored `acer-wmi.c` is GPL-2.0-or-later. Each kernel and
power-profiles-daemon patch carries the license of the upstream file it
modifies, per that file's SPDX header or existing license notice where no
SPDX identifier is present. In particular, the kernel Kconfig file is
GPL-2.0-only; the affected ppd C files state GPL-3.0 without "or later".
These descriptions do not change any upstream license. The
exported patch headers use the project address `no-reply@detello.com`;
upstream submissions carry the author's own address.
