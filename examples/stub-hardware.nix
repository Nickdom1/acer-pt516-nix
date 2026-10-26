# Stand-in for a consumer's hardware-configuration.nix, so that the examples
# evaluate and build in this repository. Use the file that
# nixos-generate-config wrote on your machine instead.
{
  fileSystems."/" = {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };
  boot.loader.systemd-boot.enable = true;
  system.stateVersion = "26.05";
}
