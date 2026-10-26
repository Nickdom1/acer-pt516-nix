# acer_wmi_pt516: the pdx86 acer-wmi driver with the PT516-52s series applied,
# built out of tree against the consumer's selected kernel.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  top = config.pt516.hardware;
  cfg = top.acerWmi;
  dir = ../../kernel/acer-wmi;

  fields =
    file:
    lib.listToAttrs (
      lib.concatMap (
        line:
        let
          m = builtins.match "([^:]+): (.*)" line;
        in
        lib.optional (m != null) (lib.nameValuePair (lib.elemAt m 0) (lib.elemAt m 1))
      ) (lib.splitString "\n" (builtins.readFile file))
    );
  base = fields (dir + "/BASE");
  baseFile = dir + "/base/acer-wmi.c";
  baseHash = builtins.hashFile "sha256" baseFile;
  # Same store name as a fetch of this commit would use, so the source path
  # does not depend on how the file reached the store.
  source = builtins.path {
    path = baseFile;
    name = "acer-wmi-${base.branch}-${builtins.substring 0 8 base.commit}.c";
    recursive = false;
    sha256 = baseHash;
  };
  series = lib.sort (a: b: a < b) (
    lib.filter (lib.hasSuffix ".patch") (lib.filesystem.listFilesRecursive (dir + "/patches"))
  );

  # GNU patch accepts offsets and fuzz, so the export could build against a
  # base file it was not made for. SOURCE records the sha256 of acer-wmi.c at
  # the export's base; refuse to evaluate any other pairing.
  exportBase = lib.concatMap (
    line:
    let
      m = builtins.match "acer-wmi\\.c at base: sha256 ([0-9a-f]{64})" line;
    in
    if m == null then [ ] else m
  ) (lib.splitString "\n" (builtins.readFile (dir + "/SOURCE")));
  module =
    lib.throwIfNot (exportBase == [ baseHash ])
      ''
        acer-pt516-nix: kernel/acer-wmi/SOURCE records acer-wmi.c at its base as sha256
        ${if exportBase == [ ] then "(no such line)" else lib.concatStringsSep " " exportBase},
        but kernel/acer-wmi/base/acer-wmi.c has sha256 ${baseHash}. Export the
        series and its base together with maintainers/sync-upstream-patches.sh.''
      (
        pkgs.callPackage ../../pkgs/acer-wmi-pt516 {
          kernel = config.boot.kernelPackages.kernel;
          inherit source;
          patches = series;
        }
      );

  testedKernel = "7.2.7";
  kernelVersion = config.boot.kernelPackages.kernel.version;
in
{
  options.pt516.hardware.acerWmi = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = top.enable;
      defaultText = lib.literalExpression "config.pt516.hardware.enable";
      description = "Build and use acer_wmi_pt516 instead of the stock acer_wmi.";
    };
    package = lib.mkOption {
      type = lib.types.package;
      default = module;
      defaultText = lib.literalMD ''
        `pkgs/acer-wmi-pt516` built against `config.boot.kernelPackages.kernel`
        from `kernel/acer-wmi/base/acer-wmi.c` and `kernel/acer-wmi/patches/*.patch`
      '';
      description = ''
        The kernel module package. It must provide acer_wmi_pt516 for the
        selected kernel. Overriding it bypasses the base-file guard.
      '';
    };
    turboKey = lib.mkOption {
      type = lib.types.enum [
        "toggle"
        "cycle"
      ];
      default = "toggle";
      description = ''
        "toggle": the Turbo key switches Turbo on and off, as on Windows
        (module option cycle_gaming_thermal_profile=0). "cycle": the driver's
        default, the key cycles through the platform profiles.
      '';
    };
    blacklistStock = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Blacklist the stock acer_wmi. Both modules carry the same WMI alias;
        with the stock module blacklisted, udev loads acer_wmi_pt516.
      '';
    };
  };

  config = lib.mkIf (top.enable && cfg.enable) {
    warnings =
      lib.optional (kernelVersion != testedKernel)
        "acer-pt516-nix: acer_wmi_pt516 has hardware acceptance only on kernel ${testedKernel} (it also compiles against 6.18.52); you are building against ${kernelVersion}";

    # Appending (not forcing) keeps other modules, such as NVIDIA's, in the
    # same list.
    boot = {
      extraModulePackages = [ cfg.package ];
      blacklistedKernelModules = lib.mkIf cfg.blacklistStock [ "acer_wmi" ];
      extraModprobeConfig = lib.mkIf (cfg.turboKey == "toggle") ''
        options acer_wmi_pt516 cycle_gaming_thermal_profile=0
      '';
    };
  };
}
