# Read-only capability check for the supported model. It reads sysfs and asks
# systemd whether services are active; it never writes to sysfs, firmware or
# any configuration. Exit status 1 when a required item is missing.
{
  writeShellApplication,
  coreutils,
  systemd,
}:

writeShellApplication {
  name = "pt516-check";
  runtimeInputs = [
    coreutils
    systemd
  ];
  text = ''
    missing=0
    pass() { printf 'PASS     %s\n' "$*"; }
    miss() { printf 'MISSING  %s\n' "$*"; missing=1; }
    note() { printf 'MISSING  %s (optional)\n' "$*"; }
    info() { printf 'INFO     %s\n' "$*"; }

    product=$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)
    if [[ $product == "Predator PT516-52s" ]]; then
      pass "DMI product name: $product"
    else
      miss "DMI product name is \"''${product:-unreadable}\", supported: \"Predator PT516-52s\""
    fi
    info "BIOS version: $(cat /sys/class/dmi/id/bios_version 2>/dev/null || echo unreadable); kernel $(uname -r)"

    if [[ -d /sys/module/acer_wmi_pt516 ]]; then
      pass "module acer_wmi_pt516 loaded"
    else
      miss "module acer_wmi_pt516 not loaded"
    fi
    if [[ -d /sys/module/acer_wmi ]]; then
      miss "stock module acer_wmi is loaded (it must be blacklisted; both claim the same WMI device)"
    else
      pass "stock module acer_wmi not loaded"
    fi

    if [[ -r /sys/firmware/acpi/platform_profile ]]; then
      pass "platform_profile: $(cat /sys/firmware/acpi/platform_profile) (choices: $(cat /sys/firmware/acpi/platform_profile_choices 2>/dev/null || echo unreadable))"
    else
      miss "/sys/firmware/acpi/platform_profile"
    fi

    found=""
    for hwmon in /sys/class/hwmon/hwmon*; do
      [[ $(cat "$hwmon/name" 2>/dev/null) == acer ]] || continue
      fans=("$hwmon"/pwm*_enable)
      [[ -e ''${fans[0]} ]] && found="$hwmon (''${#fans[@]} pwm*_enable)"
    done
    if [[ -n $found ]]; then
      pass "hwmon acer with fan modes: $found"
    else
      miss "hwmon named acer with pwm*_enable"
    fi

    for zone in left middle right; do
      led=/sys/class/leds/acer-wmi:rgb:kbd_zoned_backlight-$zone
      if [[ -d $led ]]; then
        pass "keyboard zone LED $zone"
      else
        miss "keyboard zone LED $led"
      fi
    done

    # Only checks that the attribute exists; reading it would query firmware.
    if [[ -e /sys/devices/platform/acer-wmi/kbd_backlight_timeout ]]; then
      pass "kbd_backlight_timeout attribute"
    else
      miss "/sys/devices/platform/acer-wmi/kbd_backlight_timeout"
    fi

    if systemctl is-active --quiet power-profiles-daemon.service; then
      pass "power-profiles-daemon running"
    else
      note "power-profiles-daemon not running; profile changes then bypass it"
    fi
    if systemctl is-active --quiet thermald.service; then
      pass "thermald running"
    else
      note "thermald not running; the firmware's DTT tables are then not applied"
    fi

    if (( missing )); then
      echo "Result: a required item is missing (see docs/verification.md)."
      exit 1
    fi
    echo "Result: all required items present."
  '';
}
