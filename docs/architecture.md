# Architecture and behavior

Three layers, each a separate NixOS module. Each effect has one owner.
The first half is the structure; the second half is what each layer does,
how it transitions, and the evidence behind each row.

```
firmware/EC     Acer gaming WMI: fans, Turbo LED bit, thermal profile, keyboard zones
kernel          acer_wmi_pt516: Turbo key, platform_profile, hwmon fan modes,
                keyboard zone LEDs, kbd_backlight_timeout            (hardware layer)
system          power-profiles-daemon (profile selection), thermald (DTT tables)
                turbo-boot-reset.service (once per boot)              (hardware layer)
root daemon     turbo-broker: lighting, game-profile requests, battery policy,
                snapshots and restoration                             (broker layer)
user session    turbo-agent, performance-mode units: session holds,
                memory protection, stop/restore of listed units       (policy layer)
UI              existing tools (desktop profile switcher, LACT, CoolerControl);
                they request changes through the owners above
```

## One owner per effect

| Effect | Owner | Boundary |
|---|---|---|
| Turbo on/off from the key, Turbo fans, Turbo profile | kernel driver | The broker never writes key-owned fans or the Turbo LED bit. |
| Profile selection and holds | power-profiles-daemon | The broker requests profiles through ppd (or writes sysfs when ppd is absent). |
| Keyboard color, brightness, idle timeout while Turbo or a game is active | turbo-broker | Records the original values before the first change; restores exactly those. |
| Game profile request | turbo-broker, leased to the requesting process | A request lapses 30 s after its sender exits. |
| Session hold (performance mode during Turbo), opt-out | turbo-agent (user service) | Starts and stops `performance-mode-turbo.service` through the user manager. |
| Memory protection, stopping and restoring listed units and VMs | performance-policy (user unit plus a root template unit) | Only units listed in Nix; only units that were running at entry are restarted. |
| GPU clock offsets | LACT or another GPU tool | This project has no GPU writer and no NVML code. |
| Fan curves | firmware, CoolerControl | No fan manager here; the broker only undoes fan writes it made itself. |

There is no general resource engine: runtime commands cannot add units,
paths or privileges; Nix configuration fixes them.

## Processes and privileges

- `turbo-boot-reset.service` (root, oneshot, hardware layer): on a new boot
  writes `balanced` and automatic fan mode, asks ppd for balanced, and
  records the boot ID so a same-boot restart does nothing.
- `turbo-broker.service` (root, `ProtectSystem=strict`, `ProtectHome`,
  `NoNewPrivileges`): reads `/etc/turbo-broker.json`; writes its journal
  `/run/turbo-broker/state.json` (mode 0640, group `turbo-broker-status`)
  and listens on `/run/turbo-broker/control` (0660, same group). Every
  request is checked against the peer's credentials: `game-profile on|off`
  is allowed for the group, `on`/`off` only for root.
  `RestartKillSignal=SIGUSR1` makes a restart keep active add-ons; a stop
  restores them and leaves hardware Turbo alone.
- `turbo-agent` (user service of the configured user): reads the journal
  once a second, starts its hold through the user manager's D-Bus API, and
  exposes `org.detello.TurboAgent1` (`Status()`,
  `ReleasePerformanceMode()`) on the session bus. No arbitrary unit or
  hardware command.
- `performance-mode.service` (user) and `performance-mode-system@<uid>.service`
  (root template): the session and root halves of the policy. A polkit rule
  allows only the configured user, in the policy group, to start and stop
  only that template, and the root script rejects any other UID.

## Runtime interface

These names, key sets and variables are fixed, as is every option path and
default. Existing installations and the tooling that tests them depend on
them, so changing one is a breaking release.

| Kind | Names |
|---|---|
| System units | `turbo-broker.service`, `turbo-boot-reset.service`, `performance-mode-system@<uid>.service` |
| User units | `turbo-agent.service`, `turbo-game.service`, `performance-mode.service`, `performance-mode-manual.service`, `performance-mode-app.service`, `performance-mode-turbo.service` |
| `/etc/turbo-broker.json` | keys `addons`, `games`, `onBattery`, `profileAddons`, `user`; the broker reads it at start, `TURBO_BROKER_CONFIG` overrides the path |
| `/etc/performance-policy-agent.json` | keys `gameUnit`, `games`, `hardwareSocket`, `hardwareStateFile`, `onBattery`, `performanceMode`, `profile`; a copy of the agent's configuration for external tools. The agent reads its store copy unless `TURBO_AGENT_CONFIG` names another file |
| Groups | `turbo-broker-status`; `gamemode` or, without GameMode, `performance-policy` |
| Session bus | `org.detello.TurboAgent1` at `/org/detello/TurboAgent1` |

The broker's journal `/run/turbo-broker/state.json` and socket
`/run/turbo-broker/control`, and the agent's state file
`$XDG_RUNTIME_DIR/turbo-agent/state.json`, are used by the agent and by
the author's tooling.

Whether the session policy should get a more general name and namespace is
deferred.

## Composition

- The hardware module uses the broker program for one root command (boot
  reset); it does not read broker options.
- The broker module reads `pt516.hardware` only to decide whether to
  order itself after the boot reset. It evaluates and installs without the
  hardware module and without the policy module; at runtime it still needs
  a driver that provides the Turbo profile, the `acer` hwmon fan modes and
  the keyboard zones.
- The policy module has no Acer dependency. With the broker and the policy
  both enabled, the broker module sets `services.performance-policy.agent.*`
  to follow the broker's journal and socket and to treat
  `turbo-game.service` as the game lifetime.
- GameMode: with `pt516.turbo.games.enable` the broker module enables
  GameMode, sets its governor to `powersave` (intel_pstate's performance
  governor rejects non-performance EPP) and points its start/end hooks at
  `turbo-game.service`. Without the broker, the policy module's own hooks
  hold `performance-mode-app.service` directly.

## Behavior

What each layer does, how it transitions, and its limits. How this is
tested on the machine: [verification.md](verification.md).

### Hardware layer

- The Turbo key turns Turbo on: Acer's Turbo thermal profile (`performance` in `platform_profile`), Turbo fan mode, the firmware's higher GPU budget and zero CPU thermal offset. The next press restores the previous profile and automatic fans.
- The key's state is read correctly (12-byte firmware reply), so presses alternate instead of always switching Turbo on.
- Profiles `quiet`, `balanced`, `balanced-performance`, `performance` through `platform_profile` and ppd; ppd follows profile changes made by the key or the firmware.
- Fan modes through hwmon `acer` `pwm*_enable` (Turbo = 0, automatic = 2).
- Three keyboard zones as multicolor LEDs `acer-wmi:rgb:kbd_zoned_backlight-{left,middle,right}`.
- Keyboard idle timeout `kbd_backlight_timeout` (0 = never, 1-255 s), read live from firmware.
- Turbo state retained by the firmware across a warm reboot is adopted, and the boot reset then starts the boot balanced with automatic fans. Same-boot broker restarts keep Turbo.
- thermald applies the firmware's Intel DTT tables (thermal offset per profile).
- ppd restores the powersave governor before EPP writes when another program changed it (PPD-2).

Not tested on hardware: suspend and wake, color retention across a
power-off, the 6.18.52 kernel, other firmware versions.

### Broker layer

States: `off -> entering -> on -> leaving -> off`. Transitions are
serialized; rapid key presses collapse to the final state.


| Trigger | Effect |
|---|---|
| Turbo on (key, or `performance` with Turbo fans) | Records the original keyboard color, brightness and idle timeout, then applies the configured lighting and timeout. With the policy: the agent holds `performance-mode-turbo.service`. |
| Turbo off | Restores the recorded lighting and timeout; the agent releases its hold. |
| Game starts (GameMode, `turbo-game.service`) | Requests `performance` without changing fans; applies `games.addons`. If the key turns Turbo on during the game, ownership passes to the key. |
| Game ends | Restores the previous profile only if it is still `performance` with automatic fans and the request was not yielded. |
| Game requester disappears | The request lapses 30 s after its process is gone. |
| Plain `performance` profile outside games | Applies `broker.profileAddons` (default none). |
| On battery, `onBattery = "skip-addons"` | Releases lighting, timeout and the session hold; reapplies on AC; hardware Turbo stays. |
| On battery, `"end-turbo"` (default) | Requests `balanced` through ppd (sysfs if ppd fails) once per Turbo entry; the driver ends key Turbo and fans; add-ons and the hold are released; nothing re-enters on AC. |
| On battery, `"allow"` | Nothing changes. |
| Broker restart (`systemctl restart`, a configuration switch) | Keeps active add-ons and their records. |
| Broker stop | Restores add-ons; leaves hardware Turbo to the kernel. |
| `turbo-broker on/off` (root) | Writes `performance` and Turbo fan mode, or the reverse. Does not set the key's private driver state or the firmware Turbo bit, so mixing CLI and key presses is not equivalent to key use. |

Restoration rules: restore exactly what was recorded at entry; failed
restores keep their records for retry; the journal survives broker restarts
but not a reboot. Never delete `/run/turbo-broker/state.json` while it owns
effects. Older brokers refuse newer journals; downgrade by booting the older
generation.

KDE: with lighting enabled, the module disables Kameleon's accent color
sync through `/etc/xdg/kdeglobals`, for all RGB devices Kameleon supports
(option `addons.lighting.kde.disableAccentSync`). Without it, Plasma login
overwrites the color.


### Session policy


- Holds compose: manual (`performance-mode on`), game (`performance-mode-app`), Turbo (`performance-mode-turbo`); the policy runs while any hold exists.
- Memory protection with `memory.low` on `performance.slice` and `session.slice` and their ancestors; stronger existing values kept and restored. Protects under memory pressure; it does not reserve memory.
- Stops listed desktop, user and system units and VMs that are running at entry; restarts only those, clearing systemd's start-rate counter first.
- Failed entry still restores what it stopped.
- `turbo-agent release performance-mode`: opt out of the Turbo hold until the next Turbo entry; survives agent restarts.
- Only the configured user can request the root half; other users and other UIDs are refused.

The policy works without the broker and without Acer hardware (the VM test
uses neither). Application freezing and memory reclaim are not
implemented.
