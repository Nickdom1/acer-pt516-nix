# Module-composition checks: evaluate small NixOS configurations with the
# public modules and assert on the resulting config. Nothing is built except
# a marker file; a failed expectation fails evaluation and names every
# failed check.
{
  pkgs,
  lib,
  nixosSystem,
  modules,
}:

let
  stub = {
    nixpkgs.pkgs = lib.mkDefault pkgs;
    boot.loader.grub.enable = false;
    fileSystems."/" = {
      device = "/dev/disk/by-label/nixos";
      fsType = "ext4";
    };
    system.stateVersion = "26.05";
  };
  eval = imports: (nixosSystem { modules = [ stub ] ++ imports; }).config;
  user = name: { users.users.${name}.isNormalUser = true; };
  failedAssertions = config: map (a: a.message) (lib.filter (a: !a.assertion) config.assertions);
  hasMessage = text: messages: lib.any (lib.hasInfix text) messages;
  userUnits = config: lib.attrNames config.systemd.user.services;
  check = name: ok: { inherit name ok; };

  # (1) Everything imported, nothing enabled.
  none = eval [ modules.default ];
  # (2) Hardware only.
  hw = eval [
    modules.default
    { pt516.hardware.enable = true; }
  ];
  hwPpd = hw.services.power-profiles-daemon.package;
  # (3) Broker only, policy module not imported.
  brokerOnly = eval [
    modules.broker
    (user "carol")
    {
      pt516.turbo.broker = {
        enable = true;
        user = "carol";
      };
    }
  ];
  # (4) Policy only.
  policyOnly = eval [
    modules.policy
    (user "dave")
    {
      services.performance-policy = {
        enable = true;
        user = "dave";
      };
    }
  ];
  # (5) All three with the reference values of the accepted configuration.
  all = eval [
    modules.default
    (user "alice")
    {
      pt516 = {
        hardware.enable = true;
        turbo.broker = {
          enable = true;
          user = "alice";
          onBattery = "allow";
        };
        turbo.addons.lighting.idleTimeoutSeconds = 0;
      };
      services.performance-policy = {
        enable = true;
        user = "alice";
        preset = "focused";
        protection = {
          appMiB = 8192;
          sessionMiB = 1024;
        };
      };
    }
  ];
  expectedJson = ''{"addons":{"lighting":{"brightness":255,"color":"255 32 0","enable":true,"idleTimeoutSeconds":0,"stayOn":false},"performanceMode":{"enable":true}},"games":{"addons":{"lighting":false,"performanceMode":true},"enable":true,"performanceProfile":true},"onBattery":"allow","profileAddons":[],"user":"alice"}'';
  # Negative cases.
  undeclared = eval [
    modules.broker
    {
      pt516.turbo.broker = {
        enable = true;
        user = "mallory";
      };
    }
  ];
  mismatch = eval [
    modules.default
    (user "alice")
    (user "bob")
    {
      pt516.turbo.broker = {
        enable = true;
        user = "alice";
      };
      services.performance-policy = {
        enable = true;
        user = "bob";
      };
    }
  ];
  keyNoPolicy = eval [
    modules.broker
    (user "carol")
    {
      pt516.turbo.broker = {
        enable = true;
        user = "carol";
      };
      pt516.turbo.keys.predatorSense = "performance-mode";
    }
  ];
  # ppd version guard.
  ppd031 = pkgs.extend (
    _: prev: {
      power-profiles-daemon = prev.power-profiles-daemon.overrideAttrs {
        version = "0.31";
        __intentionallyOverridingVersion = true;
      };
    }
  );
  hw031 = eval [
    modules.hardware
    {
      nixpkgs.pkgs = ppd031;
      pt516.hardware.enable = true;
    }
  ];
  hw031off = eval [
    modules.hardware
    {
      nixpkgs.pkgs = ppd031;
      pt516.hardware.enable = true;
      pt516.hardware.powerProfilesDaemon.patches = {
        spelling.enable = false;
        governor.enable = false;
      };
    }
  ];

  checks = [
    (check "none: no turbo system units" (
      !(none.systemd.services ? turbo-broker) && !(none.systemd.services ? turbo-boot-reset)
    ))
    (check "none: no policy or game user units" (
      lib.all (u: !(lib.elem u (userUnits none))) [
        "performance-mode"
        "turbo-game"
        "turbo-agent"
        "performance-mode-turbo"
      ]
    ))
    (check "none: no acer module, blacklist or modprobe option" (
      none.boot.extraModulePackages == [ ]
      && !(lib.elem "acer_wmi" none.boot.blacklistedKernelModules)
      && !(lib.hasInfix "acer_wmi_pt516" none.boot.extraModprobeConfig)
    ))
    (check "none: no groups" (
      !(none.users.groups ? turbo-broker-status) && !(none.users.groups ? performance-policy)
    ))
    (check "none: no configuration files" (
      !(none.environment.etc ? "turbo-broker.json")
      && !(none.environment.etc ? "xdg/kdeglobals")
      && !(none.environment.etc ? "performance-policy-agent.json")
    ))
    (check "none: ppd package and thermald untouched" (
      none.services.power-profiles-daemon.package.drvPath == pkgs.power-profiles-daemon.drvPath
      && !none.services.thermald.enable
    ))
    (check "none: GameMode untouched" (!none.programs.gamemode.enable))

    (check "hardware: acer_wmi_pt516 package" (
      map (p: p.pname) hw.boot.extraModulePackages == [ "acer-wmi-pt516" ]
    ))
    (check "hardware: module source is the vendored base, guard passes" (
      let
        p = lib.head hw.boot.extraModulePackages;
      in
      lib.hasSuffix "-acer-wmi-review-ilpo-next-2622c23b.c" p.src
      && lib.length p.patches == 11
      && lib.hasPrefix "/nix/store/" p.drvPath
    ))
    (check "hardware: stock blacklisted, toggle option" (
      lib.elem "acer_wmi" hw.boot.blacklistedKernelModules
      && lib.hasInfix "options acer_wmi_pt516 cycle_gaming_thermal_profile=0" hw.boot.extraModprobeConfig
    ))
    (check "hardware: boot reset" (
      hw.systemd.services ? turbo-boot-reset
      && lib.hasSuffix "/bin/turbo-broker boot-reset" hw.systemd.services.turbo-boot-reset.serviceConfig.ExecStart
      && lib.elem "acer_wmi_pt516" hw.boot.kernelModules
    ))
    (check "hardware: status group without members" (
      hw.users.groups ? turbo-broker-status && hw.users.groups.turbo-broker-status.members == [ ]
    ))
    (check "hardware: thermald and its unit policy" (
      hw.services.thermald.enable
      && hw.systemd.services.thermald.serviceConfig.Restart == "on-failure"
      && hw.systemd.services.thermald.serviceConfig.SuccessExitStatus == "2"
    ))
    (check "hardware: ppd 0.30 with both exports" (
      map baseNameOf (lib.drop (lib.length (pkgs.power-profiles-daemon.patches or [ ])) hwPpd.patches)
      == map baseNameOf [
        ../patches/power-profiles-daemon/0001-balanced-performance-spelling.patch
        ../patches/power-profiles-daemon/0002-intel-pstate-governor.patch
      ]
    ))
    (check "hardware: no broker or policy units" (
      !(hw.systemd.services ? turbo-broker)
      && !(lib.elem "performance-mode" (userUnits hw))
      && !(lib.elem "turbo-game" (userUnits hw))
      && !hw.programs.gamemode.enable
    ))
    (check "hardware: untested-kernel warning names the version" (
      hasMessage "you are building against ${hw.boot.kernelPackages.kernel.version}" hw.warnings
      || hw.boot.kernelPackages.kernel.version == "7.2.7"
    ))

    (check "broker only: carol is the only status member" (
      brokerOnly.users.groups.turbo-broker-status.members == [ "carol" ]
    ))
    (check "broker only: turbo-game for carol only, no policy dependency" (
      brokerOnly.systemd.user.services.turbo-game.unitConfig.ConditionUser == "carol"
      && brokerOnly.systemd.user.services.turbo-game.requires == [ ]
    ))
    (check "broker only: no policy units or agent" (
      lib.all (u: !(lib.elem u (userUnits brokerOnly))) [
        "performance-mode"
        "performance-mode-app"
        "turbo-agent"
        "performance-mode-turbo"
      ]
      && !(brokerOnly.environment.etc ? "performance-policy-agent.json")
    ))
    (check "broker only: no boot reset, so multi-user.target and no Wants" (
      brokerOnly.systemd.services.turbo-broker.wantedBy == [ "multi-user.target" ]
      && brokerOnly.systemd.services.turbo-broker.wants == [ ]
    ))
    (check "broker only: GameMode on with powersave governor" (
      brokerOnly.programs.gamemode.enable
      && brokerOnly.programs.gamemode.settings.general.desiredgov == "powersave"
    ))
    (check "broker only: performance-mode add-on warning" (
      hasMessage "have no effect without services.performance-policy" brokerOnly.warnings
    ))
    (check "broker only: assertions pass" (failedAssertions brokerOnly == [ ]))
    (check "broker only: json names carol" (
      (builtins.fromJSON brokerOnly.environment.etc."turbo-broker.json".text).user == "carol"
    ))

    (check "policy only: polkit rule names dave and his group" (
      lib.hasInfix ''subject.user == "dave"'' policyOnly.security.polkit.extraConfig
      && lib.elem "gamemode" policyOnly.users.users.dave.extraGroups
    ))
    (check "policy only: no Acer or broker references" (
      !(lib.hasInfix "turbo-broker" policyOnly.security.polkit.extraConfig)
      && policyOnly.boot.extraModulePackages == [ ]
      && !(policyOnly.systemd.services ? turbo-broker)
      && !(policyOnly.environment.etc ? "turbo-broker.json")
      && !(lib.elem "turbo-agent" (userUnits policyOnly))
    ))
    (check "policy only: units for dave only" (
      policyOnly.systemd.user.services.performance-mode.unitConfig.ConditionUser == "dave"
    ))

    (check "all: /etc/turbo-broker.json layout" (
      all.environment.etc."turbo-broker.json".text == expectedJson
    ))
    (check "all: agent wiring" (
      all.services.performance-policy.agent.enable
      && all.services.performance-policy.agent.gameUnit == "turbo-game.service"
      && all.services.performance-policy.agent.hardwareSocket == "/run/turbo-broker/control"
      && all.services.performance-policy.agent.onBattery == "allow"
    ))
    (check "all: broker ordering and boot reset" (
      all.systemd.services.turbo-broker.after == [
        "turbo-boot-reset.service"
        "power-profiles-daemon.service"
      ]
      && all.systemd.services.turbo-broker.wants == [ "turbo-boot-reset.service" ]
      && all.systemd.services.turbo-broker.wantedBy == [ "graphical.target" ]
    ))
    (check "all: GameMode hooks start turbo-game" (
      lib.hasSuffix "--user start --no-block turbo-game.service" all.programs.gamemode.settings.custom.start
    ))
    (check "all: game lifetime requires the app hold" (
      all.systemd.user.services.turbo-game.requires == [ "performance-mode-app.service" ]
      && all.systemd.user.services.performance-mode-app.partOf == [ "turbo-game.service" ]
    ))
    (check "all: Kameleon accent sync disabled" (
      all.environment.etc."xdg/kdeglobals".text == "[General]\nDeviceLedsAccentColored[$i]=false\n"
    ))
    (check "all: alice in the status group" (
      all.users.groups.turbo-broker-status.members == [ "alice" ]
    ))
    (check "all: assertions pass, no add-on warning" (
      failedAssertions all == [ ] && !(hasMessage "have no effect without" all.warnings)
    ))

    (check "negative: undeclared broker user is refused" (
      hasMessage ''pt516.turbo.broker.user = "mallory" must be a normal user'' (
        failedAssertions undeclared
      )
    ))
    (check "negative: broker and policy users must match" (
      hasMessage "must name the same user" (failedAssertions mismatch)
    ))
    (check "negative: PredatorSense binding needs the policy" (
      hasMessage "predatorSense = performance-mode needs" (failedAssertions keyNoPolicy)
    ))
    (check "negative: unknown ppd version fails evaluation" (
      !(builtins.tryEval hw031.services.power-profiles-daemon.package.drvPath).success
    ))
    (check "negative: unknown ppd version with patches disabled is untouched" (
      hw031off.services.power-profiles-daemon.package.drvPath == ppd031.power-profiles-daemon.drvPath
    ))
  ];
  failed = lib.filter (c: !c.ok) checks;
in
if failed != [ ] then
  throw "modules-eval failed:\n${lib.concatMapStringsSep "\n" (c: "  - ${c.name}") failed}"
else
  pkgs.runCommand "acer-pt516-nix-modules-eval" { } ''
    cat > $out <<'LIST'
    ${lib.concatMapStringsSep "\n" (c: "PASS ${c.name}") checks}
    LIST
  ''
