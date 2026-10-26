# Apply the firmware's Intel DTT tables (per-mode TCC offset 8/8/0/0 C for
# Quiet/Default/Extreme/Turbo; thermald manages PL1 itself) the way Windows
# does.
{ config, lib, ... }:

let
  top = config.pt516.hardware;
in
{
  options.pt516.hardware.thermald.enable = lib.mkOption {
    type = lib.types.bool;
    default = true;
    description = "Enable thermald, which applies the firmware's Intel DTT tables.";
  };

  config = lib.mkIf (top.enable && top.thermald.enable) {
    services.thermald.enable = true;
    # Upstream thermald's own unit has both settings: exit 1 = "re-run me in
    # fallback mode", exit 2 = "unsupported platform", no retry. The NixOS
    # unit lacks them; remove these lines once the selected nixpkgs carries
    # the fix (UPSTREAM.md, NP-1). mkDefault lets that definition win.
    systemd.services.thermald.serviceConfig = {
      Restart = lib.mkDefault "on-failure";
      SuccessExitStatus = lib.mkDefault "2";
    };
  };
}
