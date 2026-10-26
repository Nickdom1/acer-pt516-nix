# Generic opt-in policy. Host lists and desktop choices belong in host configuration.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.performance-policy;
  appMiB = cfg.protection.appMiB;
  sessionMiB = cfg.protection.sessionMiB;
  mib = n: "${toString n}M";
  inherit (cfg) systemUnits;
  userUnits =
    lib.optionals cfg.desktop.enable cfg.desktop.units ++ cfg.userUnits ++ cfg.extraUserUnits;
  unitType = lib.types.strMatching "[A-Za-z0-9_@.:\\-]+"; # \ admits systemd-escape sequences such as \x2d
  bool =
    default: description:
    lib.mkOption {
      type = lib.types.bool;
      inherit default description;
    };
  list =
    description:
    lib.mkOption {
      type = lib.types.listOf unitType;
      default = [ ];
      inherit description;
    };
  agentConfig = pkgs.writeText "turbo-agent.json" (
    builtins.toJSON {
      inherit (cfg.agent)
        hardwareStateFile
        hardwareSocket
        gameUnit
        games
        performanceMode
        profile
        onBattery
        ;
    }
  );
  agentPackage =
    pkgs.writers.writePython3Bin "turbo-agent"
      {
        libraries = [ pkgs.python3Packages.dbus-next ];
        flakeIgnore = [ "E501" ];
      }
      (
        builtins.replaceStrings [ "@CONFIG@" ] [ "${agentConfig}" ] (
          builtins.readFile ../../pkgs/turbo-agent/agent.py
        )
      );

  systemdPkg = config.systemd.package;
  group = if cfg.gameMode then "gamemode" else "performance-policy";

  # Root side. $1 = on|off, $2 = UID. Runs from
  # performance-mode-system@UID.service, which provides $RUNTIME_DIRECTORY for
  # the record of what it changed.
  systemSide = pkgs.writeShellApplication {
    name = "performance-mode-system";
    runtimeInputs = [
      systemdPkg
      pkgs.coreutils
    ];
    text = ''
            action=$1 uid=$2
            [[ $uid =~ ^[0-9]+$ && $uid == $(id -u ${lib.escapeShellArg cfg.user}) ]] || exit 2
            state=$RUNTIME_DIRECTORY
      ${lib.optionalString cfg.protection.enable ''ancestors=(user.slice "user-$uid.slice" "user@$uid.service")''}

            if [[ $action == on ]]; then
              ${lib.optionalString cfg.protection.enable ''
                for u in "''${ancestors[@]}"; do
                  low=$(systemctl show "$u" -p MemoryLow --value)
                  [[ $low =~ ^[0-9]+$ ]] || continue
                  echo "$u $low" >> "$state/memory-low"
                  target=${toString ((appMiB + sessionMiB) * 1024 * 1024)}
                  # Preserve a stronger existing protection instead of lowering it.
                  # systemctl formats infinity as text, excluded by the numeric guard.
                  if (( low < target )); then
                    systemctl set-property --runtime "$u" MemoryLow="$target" || true
                  fi
                done
              ''}

              units=(${lib.escapeShellArgs systemUnits})
              for u in "''${units[@]}"; do
                if systemctl is-active --quiet "$u" && systemctl stop "$u"; then
                  echo "$u" >> "$state/stopped"
                fi
              done

              ${lib.concatMapStringsSep "\n" (vm: ''
                mapfile -t vms < <(systemctl list-units --state=active --plain --no-legend ${lib.escapeShellArgs vm.units} | cut -d' ' -f1)
                for u in "''${vms[@]}"; do
                  if systemctl stop ${lib.optionalString (!vm.restore) "--no-block"} "$u"; then
                    ${if vm.restore then ''echo "$u" >> "$state/stopped"'' else ":"}
                  fi
                done
              '') cfg.virtualMachines}
            else
              # At shutdown the restarts would only fight the shutdown transaction.
              if [[ -f $state/stopped && $(systemctl is-system-running || true) != stopping ]]; then
                while IFS= read -r u; do
                  # Each restart by this policy counts against the unit's
                  # StartLimitBurst=; clear the counter first so fast entry/exit
                  # cycles cannot leave a unit this policy stopped refused with
                  # start-limit-hit. Only recorded (entry-active) units are touched.
                  systemctl reset-failed "$u" 2>/dev/null || true
                  systemctl start --no-block -- "$u" || true
                done < "$state/stopped"
              fi
              # Restore owned settings even if a rebuild disabled protection.
              if [[ -f $state/memory-low ]]; then
                while read -r u low; do
                  systemctl set-property --runtime "$u" MemoryLow="$low" || true
                done < "$state/memory-low"
              fi
            fi
    '';
  };

  # User side, run by the user unit performance-mode.service
  # ($RUNTIME_DIRECTORY = $XDG_RUNTIME_DIR/performance-mode).
  sessionSide = pkgs.writeShellApplication {
    name = "performance-mode-session";
    runtimeInputs = [
      systemdPkg
      pkgs.coreutils
    ]
    ++ lib.optional (cfg.soundDirectory != null) pkgs.pipewire;
    text = ''
      state=$RUNTIME_DIRECTORY
      sys="performance-mode-system@$(id -u).service"

      # Audible confirmation once a transition has finished, whatever started
      # it (key, shell, game launch); it also plays over fullscreen games.
      chime() {
        ${
          if cfg.soundDirectory == null then
            ":"
          else
            ''pw-play ${lib.escapeShellArg cfg.soundDirectory}/"$1".oga || true''
        }
      }

      if [[ $1 == on ]]; then
        units=(${lib.escapeShellArgs userUnits})
        for u in "''${units[@]}"; do
          if systemctl --user is-active --quiet "$u" && systemctl --user stop "$u"; then
            echo "$u" >> "$state/stopped"
          fi
        done
        systemctl start "$sys" \
          || echo "could not start $sys; is $(id -un) in the ${group} group (re-login after rebuild)?" >&2
        chime power-plug
      else
        systemctl stop "$sys" || true
        if [[ -f $state/stopped ]]; then
          while IFS= read -r u; do
            # Same as the root side: desktop shells commonly allow only a
            # few starts per minute.
            systemctl --user reset-failed "$u" 2>/dev/null || true
            systemctl --user start --no-block -- "$u" || true
          done < "$state/stopped"
        fi
        chime power-unplug
      fi
    '';
  };

  performance-mode = pkgs.writeShellApplication {
    name = "performance-mode";
    runtimeInputs = [
      systemdPkg
      pkgs.coreutils
      pkgs.procps
    ];
    text = ''
      usage() {
        cat >&2 <<'EOF'
      usage: performance-mode on | off | status
             performance-mode run CMD...    (Steam launch options: performance-mode run %command%)
      EOF
        exit 2
      }

      show() {
        local u=/sys/fs/cgroup/user.slice/user-$UID.slice/user@$UID.service f held=()
        if ! systemctl --user is-active --quiet performance-mode.service; then
          echo "performance mode: off"
        else
          systemctl --user is-active --quiet performance-mode-manual.service && held+=(manual)
          systemctl --user is-active --quiet performance-mode-app.service && held+=(app)
          systemctl --user is-active --quiet performance-mode-turbo.service && held+=(turbo)
          echo "performance mode: ON (held by: ''${held[*]:-nothing})"
          for f in "$XDG_RUNTIME_DIR/performance-mode/stopped" /run/performance-mode-system/"$UID"/stopped; do
            [[ -s $f ]] && echo "  stopped: $(paste -sd' ' "$f")"
          done
          echo "  memory.low: user@$UID $(numfmt --to=iec < "$u/memory.low")," \
            "performance.slice $(numfmt --to=iec 2>/dev/null < "$u/performance.slice/memory.low" || echo -)," \
            "session.slice $(numfmt --to=iec < "$u/session.slice/memory.low")"
        fi
        free -h | head -3
      }

      case ''${1:-status} in
        on) systemctl --user start performance-mode-manual.service; show ;;
        # Stopping the main unit also drops its holds (they Require it).
        off) systemctl --user stop performance-mode.service; show ;;
        status) show ;;
        run)
          shift
          (( $# )) || usage
          ${lib.optionalString cfg.disableXalia ''export PROTON_USE_XALIA="''${PROTON_USE_XALIA:-0}"''}
          # Never let a missing user bus stop the game from launching.
          if [[ -S ''${XDG_RUNTIME_DIR:-/nonexistent}/bus ]]; then
            exec systemd-run --user --scope --quiet --collect --slice=performance.slice \
              -- ${lib.optionalString cfg.gameMode "${pkgs.gamemode}/bin/gamemoderun "}"$@"
          fi
          exec ${lib.optionalString cfg.gameMode "${pkgs.gamemode}/bin/gamemoderun "}"$@"
          ;;
        *) usage ;;
      esac
    '';
  };

  systemctl = "${systemdPkg}/bin/systemctl";

  holdUnit = description: {
    inherit description;
    requires = [ "performance-mode.service" ];
    after = [ "performance-mode.service" ];
    unitConfig.ConditionUser = cfg.user;
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.coreutils}/bin/true";
    };
  };
in
{
  options.services.performance-policy = {
    enable = lib.mkEnableOption "declarative session performance policy";
    user = lib.mkOption {
      type = lib.types.str;
      example = "alice";
      description = "Normal user, declared in users.users, authorized to request this host's policy; added to the polkit group (gamemode with GameMode, otherwise performance-policy).";
    };
    preset = lib.mkOption {
      type = lib.types.enum [
        "custom"
        "protect"
        "focused"
      ];
      default = "custom";
      description = "Defaults for protection.enable and desktop.enable: custom sets neither, protect enables protection, focused enables both. Each may still be set explicitly.";
    };
    protection = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = cfg.preset != "custom";
        defaultText = lib.literalExpression ''config.services.performance-policy.preset != "custom"'';
        description = "Set memory.low on performance.slice and session.slice and raise their ancestors to appMiB + sessionMiB while held; existing stronger values are preserved.";
      };
      appMiB = lib.mkOption {
        type = lib.types.ints.unsigned;
        example = 8192;
        description = "memory.low in MiB for performance.slice (programs started by performance-mode run); required when protection is enabled.";
      };
      sessionMiB = lib.mkOption {
        type = lib.types.ints.unsigned;
        example = 1024;
        description = "memory.low in MiB for session.slice; required when protection is enabled.";
      };
    };
    desktop = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = cfg.preset == "focused";
        defaultText = lib.literalExpression ''config.services.performance-policy.preset == "focused"'';
        description = "Stop only the declared desktop units while policy is held.";
      };
      units = list "Desktop units; leave compositor/audio/session infrastructure out." // {
        example = [ "plasma-plasmashell.service" ];
      };
    };
    userUnits = list "Background user units to stop if active at entry and restore on exit." // {
      example = [ "kde-baloo.service" ];
    };
    extraUserUnits = list "Convenience additions to userUnits, using the same entry-active restore record.";
    systemUnits =
      list "Background system units, in stop order; restore only those stopped at entry."
      // {
        example = [
          "syncthing.service"
          "fwupd.service"
        ];
      };
    virtualMachines = lib.mkOption {
      default = [ ];
      type = lib.types.listOf (
        lib.types.submodule {
          options = {
            units = lib.mkOption {
              type = lib.types.nonEmptyListOf (lib.types.strMatching "[A-Za-z0-9_@.:*?\\-]+\\.service");
              description = "Explicit VM service names or template patterns; never an unfiltered discovery.";
            };
            restore = bool true "Restart entry-active VMs on exit; false leaves restart to an external controller.";
          };
        }
      );
      example = lib.literalExpression ''[ { units = [ "microvm@*.service" ]; restore = true; } ]'';
      description = "Optional VM stop policies; no VM is selected by default.";
    };
    soundDirectory = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Optional directory containing power-plug.oga and power-unplug.oga.";
    };
    disableXalia = bool false "Default PROTON_USE_XALIA to zero in the run wrapper.";
    gameMode = bool true "Enable GameMode and hold performance-mode-app.service from its custom start/end hooks. The hooks are mkDefault; with pt516.turbo.broker.enable and pt516.turbo.games.enable the broker module replaces them with turbo-game.service, which requires performance-mode-app.service when pt516.turbo.games.addons.performanceMode is set. Selects GameMode's gamemode group as the polkit group.";
    agent = {
      enable = lib.mkEnableOption "the session observer for an external hardware owner";
      hardwareStateFile = lib.mkOption {
        type = lib.types.strMatching "/.*";
        default = "/run/performance-hardware/state.json";
        description = "Read-only hardware observation journal.";
      };
      hardwareSocket = lib.mkOption {
        type = lib.types.nullOr (lib.types.strMatching "/.*");
        default = null;
        description = "Optional scoped game-profile request socket.";
      };
      gameUnit = lib.mkOption {
        type = unitType;
        default = "performance-mode-app.service";
        description = "Unit representing the game request lifetime.";
      };
      games = bool true "Observe the configured game request unit.";
      performanceMode = bool true "Hold performance-mode-turbo.service while the hardware journal is available and reports turbo = true; released by `turbo-agent release performance-mode` until the next entry, and skipped on battery with onBattery = skip-addons or end-turbo.";
      profile = bool false "Also acquire the hold for plain performance outside games.";
      onBattery = lib.mkOption {
        type = lib.types.enum [
          "allow"
          "skip-addons"
          "end-turbo"
        ];
        default = "allow";
        description = "Session-effect battery policy: skip-addons and end-turbo skip the hold on battery (with end-turbo the hardware owner also ends Turbo).";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    # This module defines users.users.<user>.extraGroups, so the name always
    # exists; require a declared normal user rather than an implicit one.
    assertions = [
      {
        assertion = config.users.users.${cfg.user}.isNormalUser;
        message = "services.performance-policy.user = ${builtins.toJSON cfg.user} must be a normal user declared in users.users.";
      }
    ];
    programs.gamemode = lib.mkIf cfg.gameMode {
      enable = true;
      settings = {
        # gamemoded's PATH is only pkexec (see the NixOS module), hence absolute
        # paths. --no-block: hooks must not delay the game's GameMode
        # registration while services and the VM stop.
        custom = {
          start = lib.mkDefault "${systemctl} --user start --no-block performance-mode-app.service";
          end = lib.mkDefault "${systemctl} --user stop --no-block performance-mode-app.service";
        };
      };
    };

    # The performance-mode-system rule below keys on this group; with GameMode,
    # its own polkit rule (CPU governor, split-lock, core parking) does too.
    # Takes effect at the next login.
    users.groups.${group} = { };
    security.polkit.enable = true;
    users.users.${cfg.user}.extraGroups = [ group ];

    security.polkit.extraConfig = ''
      polkit.addRule(function (action, subject) {
        if (action.id == "org.freedesktop.systemd1.manage-units" &&
            /^performance-mode-system@[0-9]+\.service$/.test(action.lookup("unit") || "") &&
            (action.lookup("verb") == "start" || action.lookup("verb") == "stop") &&
            subject.user == ${builtins.toJSON cfg.user} && subject.isInGroup(${builtins.toJSON group})) {
          return polkit.Result.YES;
        }
      });
    '';

    systemd = {
      services."performance-mode-system@" = {
        description = "Performance mode, system side, for UID %i";
        # A rebuild while performance mode is on must not re-run the stop or
        # restart the paused daemons.
        restartIfChanged = false;
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          RuntimeDirectory = "performance-mode-system/%i";
          ExecStart = "${systemSide}/bin/performance-mode-system on %i";
          # Also restore after failed/timed-out starts, before RuntimeDirectory removal.
          ExecStopPost = "${systemSide}/bin/performance-mode-system off %i";
          TimeoutStartSec = "5min";
        };
      };

      user = {
        slices.performance = {
          description = "Programs started through performance-mode run";
          sliceConfig = lib.optionalAttrs cfg.protection.enable { MemoryLow = mib appMiB; };
        };
        slices.session.sliceConfig = lib.optionalAttrs cfg.protection.enable {
          MemoryLow = mib sessionMiB;
        };

        services = {
          performance-mode = {
            description = "Performance mode: pause background work, protect app memory";
            unitConfig = {
              StopWhenUnneeded = true;
              ConditionUser = cfg.user;
            };
            serviceConfig = {
              Type = "oneshot";
              RemainAfterExit = true;
              RuntimeDirectory = "performance-mode";
              ExecStart = "${sessionSide}/bin/performance-mode-session on";
              ExecStopPost = "${sessionSide}/bin/performance-mode-session off";
              TimeoutStartSec = "5min";
            };
          };
          performance-mode-manual = holdUnit "Performance mode held on from the shell";
          performance-mode-app = holdUnit "Performance mode held on by GameMode";

          performance-mode-turbo = lib.mkIf cfg.agent.enable (
            holdUnit "Performance mode held by the session agent"
          );
          turbo-agent = lib.mkIf cfg.agent.enable {
            description = "Session performance policy observer";
            wantedBy = [ "default.target" ];
            after = [ "dbus.socket" ];
            unitConfig.ConditionUser = cfg.user;
            restartTriggers = [ agentConfig ];
            serviceConfig = {
              ExecStart = "${agentPackage}/bin/turbo-agent daemon";
              Restart = "on-failure";
              RestartSec = 2;
              RestartKillSignal = "SIGUSR1";
              RuntimeDirectory = "turbo-agent";
              RuntimeDirectoryMode = "0700";
              RuntimeDirectoryPreserve = "restart";
              UMask = "0077";
              NoNewPrivileges = true;
            };
          };
        };
      };
    };
    # The agent's built-in configuration at a stable path, for tools that
    # derive a variant and pass it with TURBO_AGENT_CONFIG.
    environment.etc."performance-policy-agent.json" = lib.mkIf cfg.agent.enable {
      source = agentConfig;
    };
    environment.systemPackages = [ performance-mode ] ++ lib.optional cfg.agent.enable agentPackage;
  };
}
