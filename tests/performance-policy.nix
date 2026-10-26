{ pkgs }:
let
  module = ../modules/policy;
  common = { pkgs, ... }: {
    imports = [ module ];
    virtualisation.memorySize = 768;
    users.users.alice = {
      isNormalUser = true;
      uid = 1000;
      linger = true;
    };
    users.users.bob = {
      isNormalUser = true;
      uid = 1001;
      extraGroups = [ "performance-policy" ];
    };
    systemd = {
      user.services = {
        fixture-desktop = {
          wantedBy = [ "default.target" ];
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
        fixture-helper = {
          wantedBy = [ "default.target" ];
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
        fixture-user = {
          wantedBy = [ "default.target" ];
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
        # Units with a tight start rate limit: every policy restart after the
        # boot-time start would be refused unless the policy clears the counter.
        fixture-limited = {
          wantedBy = [ "default.target" ];
          unitConfig = {
            StartLimitBurst = 1;
            StartLimitIntervalSec = "infinity";
          };
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
      };
      services = {
        fixture-background = {
          wantedBy = [ "multi-user.target" ];
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
        fixture-limited-background = {
          wantedBy = [ "multi-user.target" ];
          unitConfig = {
            StartLimitBurst = 1;
            StartLimitIntervalSec = "infinity";
          };
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
        fixture-vm-restored = {
          wantedBy = [ "multi-user.target" ];
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
        fixture-vm-external = {
          wantedBy = [ "multi-user.target" ];
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
      };
    };
    services.performance-policy = {
      enable = true;
      user = "alice";
      gameMode = false;
      preset = "protect";
      protection = {
        appMiB = 128;
        sessionMiB = 32;
      };
      desktop.units = [ "fixture-desktop.service" ];
      extraUserUnits = [ "fixture-helper.service" ];
      userUnits = [
        "fixture-user.service"
        "fixture-limited.service"
      ];
      systemUnits = [
        "fixture-background.service"
        "fixture-limited-background.service"
      ];
      virtualMachines = [
        {
          units = [ "fixture-vm-restored.service" ];
          restore = true;
        }
        {
          units = [ "fixture-vm-external.service" ];
          restore = false;
        }
      ];
      agent = {
        enable = true;
        games = false;
      };
    };
  };
in
pkgs.testers.runNixOSTest {
  name = "performance-policy";
  nodes = {
    preserve = common;
    focused = {
      imports = [ common ];
      services.performance-policy.desktop.enable = true;
    };
    disabled = { ... }: { imports = [ module ]; };
  };
  testScript = ''
    import json
    start_all()
    for node in (preserve, focused):
        node.wait_for_unit("user@1000.service")
        node.wait_for_unit("fixture-background.service")
        prefix = "runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
        def user(command):
            return node.succeed(prefix + command)
        def wait_active(unit, state="active"):
            node.wait_until_succeeds(prefix + "sh -c 'test $(systemctl --user show -P ActiveState " + unit + ") = " + state + "'")
        wait_active("fixture-desktop.service")
        wait_active("fixture-helper.service")
        wait_active("fixture-user.service")
        wait_active("fixture-limited.service")
        node.wait_for_unit("fixture-limited-background.service")
        wait_active("turbo-agent.service")
        wait_active("graphical-session.target", "inactive")
        denial = node.fail("runuser -u bob -- systemctl --no-ask-password start performance-mode-system@1000.service 2>&1")
        assert "access denied" in denial.lower() or "interactive authentication required" in denial.lower(), denial
        node.succeed("test $(systemctl show -P ExecMainStartTimestampMonotonic performance-mode-system@1000.service) = 0")
        node.succeed("systemctl set-property --runtime user.slice MemoryLow=2097152")
        node.succeed("systemctl set-property --runtime user-1000.slice MemoryLow=infinity")
        print("MemoryLow infinity representation: " + node.succeed("systemctl show -P MemoryLow user-1000.slice"))
        # This request passes polkit; the root script rejects the wrong UID.
        node.fail(prefix + "systemctl --no-ask-password start performance-mode-system@0.service")
        # Fail after the root entry work. Its off path must run before systemd
        # removes the snapshots, then a normal cycle must keep the original.
        node.succeed("mkdir -p /run/systemd/system/performance-mode-system@1000.service.d; printf '[Service]\\nExecStartPost=/run/current-system/sw/bin/false\\n' > /run/systemd/system/performance-mode-system@1000.service.d/90-fail.conf; systemctl daemon-reload")
        node.fail("systemctl start performance-mode-system@1000.service")
        node.succeed("test $(cat /sys/fs/cgroup/user.slice/memory.low) = 2097152")
        node.wait_for_unit("fixture-background.service")
        node.wait_for_unit("fixture-limited-background.service")  # restarted despite its start limit
        node.wait_for_unit("fixture-vm-restored.service")
        node.succeed("test $(systemctl show -P ActiveState fixture-vm-external.service) = inactive")
        node.succeed("rm /run/systemd/system/performance-mode-system@1000.service.d/90-fail.conf; systemctl daemon-reload; systemctl reset-failed performance-mode-system@1000.service; systemctl start fixture-vm-external.service")
        # The session-side stop record must also survive an unsuccessful start.
        node.succeed("mkdir -p /run/user/1000/systemd/user/performance-mode.service.d; printf '[Service]\\nExecStartPost=/run/current-system/sw/bin/false\\n' > /run/user/1000/systemd/user/performance-mode.service.d/90-fail.conf")
        user("systemctl --user daemon-reload")
        node.fail(prefix + "performance-mode on")
        wait_active("fixture-helper.service")
        wait_active("fixture-user.service")
        wait_active("fixture-limited.service")  # restarted despite its start limit
        wait_active("fixture-desktop.service")
        node.wait_for_unit("fixture-background.service")
        node.wait_for_unit("fixture-limited-background.service")
        node.succeed("test $(cat /sys/fs/cgroup/user.slice/memory.low) = 2097152")
        node.succeed("rm /run/user/1000/systemd/user/performance-mode.service.d/90-fail.conf; systemctl start fixture-vm-external.service")
        user("systemctl --user daemon-reload")
        user("systemctl --user reset-failed performance-mode.service")  # only the unit the failing drop-in broke
        user("performance-mode on")
        wait_active("fixture-helper.service", "inactive")
        wait_active("fixture-user.service", "inactive")
        wait_active("fixture-limited.service", "inactive")
        wait_active("fixture-desktop.service", "inactive" if node == focused else "active")
        node.succeed("test $(systemctl show -P ActiveState fixture-background.service) = inactive")
        node.succeed("test $(systemctl show -P ActiveState fixture-limited-background.service) = inactive")
        node.succeed("test $(systemctl show -P ActiveState fixture-vm-restored.service) = inactive")
        node.succeed("test $(systemctl show -P ActiveState fixture-vm-external.service) = inactive")
        node.succeed("test $(cat /sys/fs/cgroup/user.slice/memory.low) = 167772160")
        node.succeed("test $(cat /sys/fs/cgroup/user.slice/user-1000.slice/memory.low) = max")
        # An app owner survives release of the manual owner.
        user("systemctl --user start performance-mode-app.service")
        user("systemctl --user stop performance-mode-manual.service")
        wait_active("performance-mode.service")
        user("systemctl --user stop performance-mode-app.service")
        wait_active("performance-mode.service", "inactive")
        wait_active("fixture-helper.service")
        wait_active("fixture-user.service")
        wait_active("fixture-limited.service")
        wait_active("fixture-desktop.service")
        node.wait_for_unit("fixture-background.service")
        node.wait_for_unit("fixture-limited-background.service")
        node.wait_for_unit("fixture-vm-restored.service")
        node.succeed("test $(systemctl show -P ActiveState fixture-vm-external.service) = inactive")
        node.succeed("test $(cat /sys/fs/cgroup/user.slice/memory.low) = 2097152")
        node.succeed("test $(cat /sys/fs/cgroup/user.slice/user-1000.slice/memory.low) = max")
        node.succeed("systemctl set-property --runtime user.slice MemoryLow=0")
        node.succeed("systemctl set-property --runtime user-1000.slice MemoryLow=0")
        # Test the real user agent, session bus and service jobs without Acer.
        def hardware(turbo, entry):
            state = json.dumps({"observed": {"available": True, "turbo": turbo, "entry": entry, "profile": "performance" if turbo else "balanced", "onBattery": False}})
            node.succeed("mkdir -p /run/performance-hardware; printf '%s' '" + state + "' > /run/performance-hardware/state.json")
        hardware(False, 0)
        user("systemctl --user start turbo-agent.service")
        hardware(True, 1)
        wait_active("performance-mode-turbo.service")
        user("turbo-agent release performance-mode")
        wait_active("performance-mode-turbo.service", "inactive")
        user("systemctl --user restart turbo-agent.service")
        node.sleep(2)
        wait_active("performance-mode-turbo.service", "inactive")
        assert json.loads(user("turbo-agent status --json"))["optOut"]
        hardware(True, 2)
        wait_active("performance-mode-turbo.service")
        hardware(False, 2)
        wait_active("performance-mode-turbo.service", "inactive")
        wait_active("fixture-helper.service")
        wait_active("fixture-limited.service")
        node.wait_for_unit("fixture-background.service")
        node.wait_for_unit("fixture-limited-background.service")
        # A unit stranded before entry is not this policy's effect: it is
        # neither recorded nor reset nor restarted.
        node.succeed("systemctl stop fixture-limited-background.service")
        node.fail("systemctl start fixture-limited-background.service")
        user("performance-mode on")
        user("performance-mode off")
        node.wait_for_unit("fixture-background.service")
        node.succeed("test $(systemctl show -P ActiveState fixture-limited-background.service) = failed")
        node.succeed("test $(systemctl show -P Result fixture-limited-background.service) = start-limit-hit")
    disabled.wait_for_unit("multi-user.target")
    disabled.fail("test -e /etc/systemd/user/performance-mode.service")
    disabled.fail("command -v turbo-agent")
  '';
}
