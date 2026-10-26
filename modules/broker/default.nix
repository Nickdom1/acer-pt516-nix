# Optional broker layer: the root hardware owner (turbo-broker) for Turbo
# observation, scoped game-profile requests, keyboard lighting and its
# restoration, plus the GameMode lifetime unit. It does not enable any
# session policy; services.performance-policy (modules/policy) is connected
# only when the consumer enables it. See docs/architecture.md.
{
  config,
  options,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.pt516.turbo;
  systemctl = "${config.systemd.package}/bin/systemctl";
  # Definitions under services.performance-policy are an error unless the
  # policy module is imported, even inside mkIf false; so test options.
  hasPolicy = options.services ? performance-policy;
  policyOn = hasPolicy && config.services.performance-policy.enable;
  hardware = config.pt516.hardware or { };
  bootReset = (hardware.enable or false) && (hardware.bootReset.enable or false);
  user = cfg.broker.user;
  # Legacy PredatorSense binding: toggles the policy's manual hold for the
  # configured user.
  toggle = pkgs.writeShellApplication {
    name = "performance-mode-key";
    runtimeInputs = [
      config.systemd.package
      pkgs.coreutils
      pkgs.util-linux
    ];
    text = ''
      uid=$(id -u ${lib.escapeShellArg user})
      [[ -S /run/user/$uid/systemd/private ]] || exit 0
      as_user() {
        systemctl --user --machine=${lib.escapeShellArg "${user}@.host"} "$@"
      }
      state=$(as_user show -P ActiveState performance-mode.service)
      [[ $state != activating && $state != deactivating ]] || exit 0
      if as_user is-active --quiet performance-mode-manual.service; then
        as_user stop performance-mode-manual.service
      else
        as_user start performance-mode-manual.service
      fi
    '';
  };
  bool =
    default: description:
    lib.mkOption {
      type = lib.types.bool;
      inherit default description;
    };
in
{
  options.pt516.turbo = {
    broker = {
      enable = bool false "Enable the Turbo broker (root hardware owner) and its GameMode lifetime unit.";
      user = lib.mkOption {
        type = lib.types.str;
        example = "alice";
        description = ''
          The one normal user (declared in users.users) whose session uses
          the broker. Joins the turbo-broker-status group (read the hardware
          journal, send game-profile requests; re-login needed) and is the
          only user for whom turbo-game.service runs.
        '';
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = pkgs.callPackage ../../pkgs/turbo-broker { };
        defaultText = lib.literalMD "the `turbo-broker` package built from this repository's `pkgs/turbo-broker`";
        description = ''
          The broker executable run by turbo-broker.service. The hardware
          layer's boot reset always uses the package built from this
          repository's source.
        '';
      };
      onBattery = lib.mkOption {
        type = lib.types.enum [
          "allow"
          "skip-addons"
          "end-turbo"
        ];
        default = "end-turbo";
        description = ''
          What happens when the laptop runs on battery while Turbo is on.
          "end-turbo" (default): the broker ends Turbo by requesting the balanced
          profile through power-profiles-daemon, so the fans, lighting, idle timeout
          and desktop hold all return to normal; nothing re-enters on AC, and a Turbo
          key press on battery is ended again on the broker's next poll.
          "allow": nothing changes on battery; Turbo, lighting, the idle timeout and
          the desktop hold stay until the Turbo key ends Turbo.
          "skip-addons": the add-ons (lighting, idle timeout, desktop hold)
          are released on battery and reapplied on AC, while the key-owned Turbo
          profile and fans stay as they are.
        '';
      };
      profileAddons = lib.mkOption {
        type = lib.types.listOf (
          lib.types.enum [
            "performanceMode"
            "lighting"
          ]
        );
        default = [ ];
        description = "Enabled add-ons requested by a plain performance profile outside GameMode, without Turbo fans. Empty keeps profile selection independent of the add-ons.";
      };
    };
    games = {
      enable = bool true "Use the configurable GameMode policy (Steam: gamemoderun %command%). Enables GameMode.";
      performanceProfile = bool true "Request the performance profile for GameMode, yielding ownership to the Turbo key and external selections; never request max fans.";
      addons = {
        performanceMode = bool true "Hold performance-mode-app.service for the GameMode lifetime (needs services.performance-policy).";
        lighting = bool false "Apply the Turbo lighting settings during GameMode.";
      };
    };
    addons = {
      performanceMode.enable = bool true "Hold performance-mode-turbo.service while the Turbo profile is active (needs services.performance-policy).";
      lighting = {
        enable = bool true "Apply the configured keyboard color in Turbo.";
        color = lib.mkOption {
          type = lib.types.addCheck (lib.types.strMatching "[0-9]{1,3} [0-9]{1,3} [0-9]{1,3}") (
            value: lib.all (part: lib.toInt part <= 255) (lib.splitString " " value)
          );
          default = "255 32 0";
          description = "Turbo color as R G B, each channel 0-255.";
        };
        brightness = lib.mkOption {
          type = lib.types.ints.between 0 255;
          default = 255;
          description = "Keyboard brightness in Turbo.";
        };
        stayOn = bool false "Keep the Turbo color when releasing add-ons.";
        idleTimeoutSeconds = lib.mkOption {
          type = lib.types.nullOr (lib.types.ints.between 0 255);
          default = null;
          description = "Keyboard idle timeout while lighting is applied: 0 never, 1-255 seconds, null unchanged. Restore the original timeout on release.";
        };
        kde.disableAccentSync = bool true ''
          Write /etc/xdg/kdeglobals with DeviceLedsAccentColored=false (immutable)
          so that KDE's Kameleon does not overwrite the broker's color at
          Plasma login. This disables Kameleon's accent color sync for ALL
          supported RGB devices on the machine, not only the keyboard zones.
        '';
      };
    };
    keys.predatorSense = lib.mkOption {
      type = lib.types.enum [
        "none"
        "gui"
        "performance-mode"
      ];
      default = "none";
      description = ''
        PredatorSense key action. "performance-mode" toggles the session
        policy's manual hold (needs services.performance-policy and the
        broker); "gui" is reserved and fails evaluation.
      '';
    };
  };

  config = lib.mkMerge (
    [
      {
        assertions = [
          {
            assertion = cfg.keys.predatorSense != "gui";
            message = "pt516.turbo.keys.predatorSense = \"gui\" is reserved and not implemented; use \"none\" or \"performance-mode\".";
          }
          {
            assertion = cfg.keys.predatorSense != "performance-mode" || (policyOn && cfg.broker.enable);
            message = "pt516.turbo.keys.predatorSense = performance-mode needs services.performance-policy.enable and pt516.turbo.broker.enable.";
          }
        ];
      }
      (lib.mkIf cfg.broker.enable {
        assertions = [
          {
            assertion = (config.users.users.${user} or { }).isNormalUser or false;
            message = "pt516.turbo.broker.user = ${builtins.toJSON user} must be a normal user declared in users.users.";
          }
          {
            assertion = !policyOn || config.services.performance-policy.user == user;
            message = "pt516.turbo.broker.user and services.performance-policy.user must name the same user: the session agent reads the broker's journal as that user.";
          }
        ];
        warnings =
          lib.optional (!policyOn && (cfg.addons.performanceMode.enable || cfg.games.addons.performanceMode))
            ''
              pt516.turbo.addons.performanceMode.enable and pt516.turbo.games.addons.performanceMode
              have no effect without services.performance-policy (modules/policy, enabled):
              there is no session hold to start. Set them to false to silence this warning.'';

        users.groups.turbo-broker-status.members = [ user ];
        environment = {
          systemPackages = [ cfg.broker.package ];
          # Frozen interface: docs/architecture.md, "Runtime interface".
          etc."turbo-broker.json".text = builtins.toJSON {
            inherit user;
            inherit (cfg.broker) onBattery profileAddons;
            addons = cfg.addons // {
              lighting = builtins.removeAttrs cfg.addons.lighting [ "kde" ];
            };
            inherit (cfg) games;
          };
          # KDE's native setting owns this choice; do not race it with sysfs polls.
          etc."xdg/kdeglobals" =
            lib.mkIf (cfg.addons.lighting.enable && cfg.addons.lighting.kde.disableAccentSync)
              {
                text = ''
                  [General]
                  DeviceLedsAccentColored[$i]=false
                '';
              };
        };
        systemd.services.turbo-broker = {
          description = "Acer Turbo add-on broker";
          after = [
            "turbo-boot-reset.service"
            "power-profiles-daemon.service"
          ];
          wants = lib.optional bootReset "turbo-boot-reset.service";
          # powerprofilesctl: onBattery = end-turbo ends Turbo through ppd.
          path = [ config.services.power-profiles-daemon.package ];
          wantedBy = [ (if bootReset then "graphical.target" else "multi-user.target") ];
          restartTriggers = [ config.environment.etc."turbo-broker.json".source ];
          stopIfChanged = false; # nixos-rebuild switch must use restart/SIGUSR1.
          serviceConfig = {
            ExecStart = "${cfg.broker.package}/bin/turbo-broker daemon";
            Restart = "on-failure";
            RestartSec = 2;
            # Explicit restart preserves add-ons. A stop/SIGTERM restores them.
            RestartKillSignal = "SIGUSR1";
            KillMode = "mixed";
            TimeoutStopSec = 15;
            RuntimeDirectory = "turbo-broker";
            RuntimeDirectoryMode = "0750";
            RuntimeDirectoryPreserve = true;
            Group = "turbo-broker-status";
            ProtectSystem = "strict";
            ProtectHome = true;
            NoNewPrivileges = true;
          };
        };
      })
      (lib.mkIf (cfg.broker.enable && cfg.games.enable) {
        programs.gamemode = {
          enable = true;
          # Intel P-State's performance governor rejects balanced/power EPP.
          # ppd owns CPU preferences, including the optional game profile hold;
          # keep GameMode's governor compatible with every ppd profile.
          settings.general = {
            desiredgov = "powersave";
            igpu_desiredgov = "powersave";
          };
          settings.custom = {
            start = "${systemctl} --user start --no-block turbo-game.service";
            end = "${systemctl} --user stop --no-block turbo-game.service";
          };
        };
        systemd.user.services.turbo-game = {
          description = "GameMode policy lifetime";
          requires = lib.optional (
            policyOn && cfg.games.addons.performanceMode
          ) "performance-mode-app.service";
          after = lib.optional (policyOn && cfg.games.addons.performanceMode) "performance-mode-app.service";
          unitConfig.ConditionUser = user;
          serviceConfig = {
            Type = "oneshot";
            RemainAfterExit = true;
            ExecStart = "${pkgs.coreutils}/bin/true";
          };
        };
      })
      (lib.mkIf (cfg.keys.predatorSense == "performance-mode") {
        services.actkbd = {
          enable = true;
          bindings = [
            {
              keys = [ 425 ];
              events = [ "key" ];
              command = "${systemctl} start --no-block performance-mode-key.service";
            }
          ];
        };
        systemd.services.performance-mode-key = {
          description = "PredatorSense key: legacy manual performance-mode hold";
          serviceConfig = {
            Type = "oneshot";
            ExecStart = "${toggle}/bin/performance-mode-key";
          };
        };
      })
    ]
    ++ lib.optional hasPolicy (
      lib.mkIf (cfg.broker.enable && policyOn) {
        # Configuration, not code, connects Turbo and game requests to the
        # session policy: the agent observes the broker's journal and requests
        # game profiles through its socket.
        services.performance-policy.agent = {
          enable = true;
          hardwareStateFile = "/run/turbo-broker/state.json";
          hardwareSocket = "/run/turbo-broker/control";
          gameUnit = "turbo-game.service";
          games = cfg.games.enable;
          performanceMode = cfg.addons.performanceMode.enable;
          profile = builtins.elem "performanceMode" cfg.broker.profileAddons;
          inherit (cfg.broker) onBattery;
        };
        systemd.user.services.performance-mode-app =
          lib.mkIf (cfg.games.enable && cfg.games.addons.performanceMode)
            {
              partOf = [ "turbo-game.service" ];
            };
      }
    )
  );
}
