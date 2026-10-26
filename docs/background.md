# Background

Why a quirk was not enough, why every effect has one owner, and why the
bundle is patch files against a vendored base. Firmware facts come from the
machine's own ACPI tables and WMI class description, read without writing
anything.

## Why a DMI quirk was not enough

Linux's `acer-wmi` driver already supports Turbo on several Predator
models; on the PT516-52s the key event reached the driver and was dropped
for want of a DMI quirk. Adding the quirk exposed three more problems. The
firmware answers the Turbo-state query with 12 bytes and the driver's helper
accepted only 4 or 8, so the state always read as zero and every press
switched Turbo on again with the fans at maximum. The firmware wants the
Turbo LED state as `0x10`/`0x11` and rejects `0`/`1` with a status the
driver never checked. And the key only switched the fan mode: the behavior
Windows calls Turbo comes from the thermal-profile write, which selects the
Intel DTT table and makes the NVIDIA platform controller re-read its
budget. The series fixes all three and makes the key select the Turbo
profile and restore the previous one.

User space had a matching pair: power-profiles-daemon 0.30 matched
`balanced_performance` while the kernel writes `balanced-performance`
(PPD-1), and with GameMode's default performance governor ppd's EPP writes
failed with EBUSY (PPD-2). The keyboard idle timeout uses the encoding
documented by the Linuwu-Sense and OpenSense projects; the hotkey value was
confirmed in this machine's SMBIOS type 170 table. No measurement here is a
claim about game performance.

## Two writers, one state

Several bugs had one cause: more than one program wrote the same state (a
released game hold made ppd cancel the key's Turbo; a boot started in Turbo
because the firmware retained it; Plasma's Kameleon rewrote the keyboard
color at login). So every effect has one owner, recorded in
[architecture.md](architecture.md): the kernel owns the key's Turbo; the
root broker owns lighting and game-profile requests and restores exactly
what it recorded; the session agent owns its hold; the policy restarts only
the units it stopped; a boot starts balanced once.

## Patch files against a vendored base

The fixes are prepared as upstream patch series, so this project follows no
kernel fork: it ships patch files exported from exact commits plus the one
upstream driver file they apply to. That file's commit is reachable only
from a moving review branch and a fetch by commit may stop resolving once
the branch is rebased, so the file is vendored and an evaluation-time hash
check ties it to the export. The power-profiles-daemon patches apply only
to the version they were prepared for. Each fix leaves the bundle when the
consumer's selected kernel or nixpkgs contains it, after the upstream-only
path has been tested ([maintainers/README.md](../maintainers/README.md)).

## Credits

- The Linux `acer-wmi` driver and its existing Predator gaming and Turbo
  support, and the platform-drivers-x86 maintainers.
- JafarAkhondali's acer-predator-turbo-and-rgb-keyboard-linux-module (the
  facer driver), the long-standing out-of-tree route for Predator Turbo
  and RGB keyboards on Linux.
- Linuwu-Sense and OpenSense, for the keyboard backlight timeout encoding.
- nixos-hardware, for the GPU and laptop profile route.
- power-profiles-daemon and thermald upstream.

This project does not claim to have invented Acer Turbo support on Linux.
It adds support for one model and fixes the problems that model exposed.
