# power-profiles-daemon fixes, applied to the consumer's own package. The
# patches were verified for one release only; any other version fails
# evaluation instead of being patched blindly or silently left unpatched.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  top = config.pt516.hardware;
  cfg = top.powerProfilesDaemon.patches;
  version = pkgs.power-profiles-daemon.version;
  verified = cfg.versions.${version} or null;
  selected =
    lib.optional cfg.spelling.enable verified.spelling
    ++ lib.optional cfg.governor.enable verified.governor;
  package =
    if verified == null then
      throw ''
        acer-pt516-nix: power-profiles-daemon ${version} has no verified patch set;
        the patches were verified for ${lib.concatStringsSep ", " (lib.attrNames cfg.versions)} only.
        Disable them with
          pt516.hardware.powerProfilesDaemon.patches.spelling.enable = false;
          pt516.hardware.powerProfilesDaemon.patches.governor.enable = false;
        or use a acer-pt516-nix release that supports ${version}.''
    else
      pkgs.power-profiles-daemon.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ selected;
      });
in
{
  options.pt516.hardware.powerProfilesDaemon.patches = {
    spelling.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Recognize the kernel's "balanced-performance" platform profile
        spelling. Without it, the firmware's non-Turbo performance profile
        set from the kernel side leaves ppd reporting a stale profile.
      '';
    };
    governor.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Restore the powersave governor before writing EPP while intel_pstate
        is active. Without it, a program that switched to the performance
        governor (for example GameMode's default) makes ppd's profile changes
        fail with EBUSY. With it, ppd overrides that program's governor when
        it applies a profile (UPSTREAM.md, PPD-2).
      '';
    };
    versions = lib.mkOption {
      type = lib.types.attrsOf (
        lib.types.submodule {
          options = {
            spelling = lib.mkOption { type = lib.types.path; };
            governor = lib.mkOption { type = lib.types.path; };
          };
        }
      );
      internal = true;
      default = {
        "0.30" = {
          spelling = ../../patches/power-profiles-daemon/0001-balanced-performance-spelling.patch;
          governor = ../../patches/power-profiles-daemon/0002-intel-pstate-governor.patch;
        };
      };
      description = "Verified patch files per power-profiles-daemon version.";
    };
  };

  config = lib.mkIf (top.enable && (cfg.spelling.enable || cfg.governor.enable)) {
    services.power-profiles-daemon.package = package;
  };
}
