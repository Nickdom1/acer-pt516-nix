# Each new boot starts balanced with automatic fans. A boot is an explicit
# off boundary; same-boot restarts of the broker keep Turbo. Runs after ppd
# starts and before the broker observes hardware; the helper also waits for
# ppd's D-Bus setter, which is serialized after its startup restore.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  top = config.pt516.hardware;
  # The hardware layer runs one root command of the broker program; it does
  # not read the broker's options.
  brokerPackage = pkgs.callPackage ../../pkgs/turbo-broker { };
in
{
  options.pt516.hardware.bootReset.enable = lib.mkOption {
    type = lib.types.bool;
    default = true;
    description = ''
      Start each boot with the balanced profile and automatic fans
      (turbo-boot-reset.service). Same-boot broker restarts keep Turbo.
      Also loads acer_wmi_pt516 at boot and creates the
      turbo-broker-status group.
    '';
  };

  config = lib.mkIf (top.enable && top.bootReset.enable) {
    # Only when this configuration builds the module; otherwise
    # systemd-modules-load would fail every boot.
    boot.kernelModules = lib.mkIf top.acerWmi.enable [ "acer_wmi_pt516" ];
    users.groups.turbo-broker-status = { };
    systemd.services.turbo-boot-reset = {
      description = "Reset Predator Turbo policy for a new boot";
      wantedBy = [ "graphical.target" ];
      wants = [ "power-profiles-daemon.service" ];
      after = [
        "systemd-modules-load.service"
        "power-profiles-daemon.service"
      ];
      before = [ "turbo-broker.service" ];
      restartIfChanged = false;
      path = [ config.services.power-profiles-daemon.package ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${brokerPackage}/bin/turbo-broker boot-reset";
        RuntimeDirectory = "turbo-broker";
        RuntimeDirectoryMode = "0750";
        RuntimeDirectoryPreserve = true;
        Group = "turbo-broker-status";
        ProtectSystem = "strict";
        ProtectHome = true;
        NoNewPrivileges = true;
      };
    };
  };
}
