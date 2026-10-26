# Hardware layer: the PT516-52s driver fixes and the system integration they
# need (thermald, power-profiles-daemon, the boot reset). Nothing here stops
# desktop sessions, user units or VMs. Every sub-option takes effect only
# with pt516.hardware.enable.
{ lib, ... }:

{
  imports = [
    ./acer-wmi.nix
    ./boot-reset.nix
    ./thermald.nix
    ./power-profiles-daemon.nix
  ];

  options.pt516.hardware = {
    enable = lib.mkEnableOption "the Acer Predator PT516-52s hardware integration";
    model = lib.mkOption {
      type = lib.types.enum [ "pt516-52s" ];
      default = "pt516-52s";
      description = ''
        The supported model; "pt516-52s" is the only value and selects
        nothing. The driver quirk matches DMI product name
        "Predator PT516-52s" only.
      '';
    };
  };
}
