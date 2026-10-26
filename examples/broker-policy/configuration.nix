# All three layers for one user, "alice": hardware, the Turbo broker and the
# session policy, connected by configuration. Replace the user name and the
# unit lists with your own.
#
# Group membership and what it grants: docs/configuration.md, "Users and groups".
_:

{
  users.users.alice = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
  };

  pt516.hardware.enable = true;

  pt516.turbo = {
    broker = {
      enable = true;
      user = "alice";
      onBattery = "end-turbo"; # or "allow" / "skip-addons" (docs/configuration.md)
    };
    addons.lighting = {
      enable = true;
      color = "255 32 0";
      brightness = 255;
      idleTimeoutSeconds = 0; # keep the keyboard lit during Turbo
    };
  };

  services.performance-policy = {
    enable = true;
    user = "alice";
    # "protect": memory.low for games started with `performance-mode run`
    # and for the session. "focused" also stops desktop.units.
    preset = "protect";
    protection = {
      appMiB = 4096;
      sessionMiB = 512;
    };
    desktop.units = [ ];
    # Stopped while the policy is held, restarted afterwards, only if they
    # were running at entry. Lists are empty by default.
    userUnits = [ ];
    systemUnits = [ ];
    virtualMachines = [ ];
  };
}
