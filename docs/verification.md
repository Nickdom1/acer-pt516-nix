# Verification and rollback

The installed-system inspection commands below only read state.

## After installing

```sh
nix run .#pt516-check     # from a checkout of this repository
```

`pt516-check` prints `PASS` or `MISSING` for: DMI product name, module
`acer_wmi_pt516` loaded and stock `acer_wmi` not loaded,
`/sys/firmware/acpi/platform_profile`, hwmon `acer` with `pwm*_enable`, the
three keyboard zone LEDs, the `kbd_backlight_timeout` attribute, and
(optional) power-profiles-daemon and thermald running. It exits 1 when a
required item is missing. It does not read the timeout value, because that
queries firmware.

Manual reads:

```sh
cat /sys/firmware/acpi/platform_profile /sys/firmware/acpi/platform_profile_choices
grep -H . /sys/class/hwmon/hwmon*/pwm*_enable      # acer hwmon: 2 = automatic, 0 = Turbo
ls /sys/class/leds | grep kbd_zoned_backlight
powerprofilesctl get
modinfo -F filename acer_wmi_pt516
turbo-broker status --json      # broker layer; needs turbo-broker-status membership
turbo-agent status --json       # policy layer with the agent
performance-mode status
journalctl -b -u turbo-broker -u turbo-boot-reset
```

Expected after a fresh boot with the default boot reset: profile
`balanced`, fan modes `2`, broker phase `off`. Pressing the Turbo key once
gives `performance` and fan modes `0`; pressing it again returns to the
previous profile and `2`.

## Rollback

Choose the previous NixOS generation in the boot menu. The driver, its
blacklist and the units belong to the generation, so booting the previous
one restores the previous driver and policy. Switching back at runtime is
not supported for the driver; reboot. Pinning the previous acer-pt516-nix commit
and rebuilding also works.

## How this is tested on the machine

Hardware acceptance was run on the author's machine on 2026-10-06 against
revision `3032382a55519b389587e707eb56afba84b5cb86`.

The installed-system suite covers a regression
group, a key, game and session acceptance group, a keyboard timeout group,
the battery policies (one cycle per policy, unplugging and replugging the
charger), and a warm-reboot pair for the boot reset. The suite is not
included in this repository. Not tested on hardware: suspend and wake, color
retention across a power-off, the 6.18.52 kernel, other firmware versions.

The repository's own checks (`nix flake check`) are:

| Check | What it runs |
|---|---|
| `unit-tests` | Python unit tests of `turbo-broker` and `turbo-agent` (83 tests). |
| `acer-wmi-harness` | Applies the patches to the vendored base, checks the result against the head hash in `kernel/acer-wmi/SOURCE`, and compiles the driver's Turbo, WMI-reply, LED and timeout functions against mocked firmware. |
| `modules-eval` | Evaluates module combinations: nothing enabled, hardware only, broker only, policy only, all three, and refused configurations (undeclared user, mismatched users, unknown ppd version). |
| `performance-policy` | NixOS VM test of the session policy and the agent, without Acer hardware. |

No check builds the kernel module or the patched power-profiles-daemon;
`modules-eval` only evaluates them. Release step 5 builds both
([maintainers/README.md](../maintainers/README.md)).

None of these replaces the run on the machine; a change to driver or
broker behavior gets that run before a release.
