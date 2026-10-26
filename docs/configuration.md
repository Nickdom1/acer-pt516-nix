# Configuration

Every option of the three modules, with its default. Options of a layer
take effect only when that layer is enabled.

## Hardware: `pt516.hardware` (`nixosModules.hardware`)

| Option | Default | Meaning |
|---|---|---|
| `enable` | `false` | Enable the hardware layer. |
| `model` | `"pt516-52s"` | The only supported model. |
| `acerWmi.enable` | `= enable` | Build and use `acer_wmi_pt516`. |
| `acerWmi.package` | built from `kernel/acer-wmi` for `boot.kernelPackages.kernel` | The module package. Overriding it skips the base-file guard. |
| `acerWmi.turboKey` | `"toggle"` | `"toggle"`: the key switches Turbo on and off (`cycle_gaming_thermal_profile=0`). `"cycle"`: the driver default, cycling profiles. |
| `acerWmi.blacklistStock` | `true` | Blacklist the stock `acer_wmi` (same WMI alias). |
| `bootReset.enable` | `true` | `turbo-boot-reset.service`: each boot starts balanced with automatic fans. Also loads `acer_wmi_pt516` early and creates the `turbo-broker-status` group. |
| `thermald.enable` | `true` | Enable thermald with `Restart=on-failure` and `SuccessExitStatus=2`. |
| `powerProfilesDaemon.patches.spelling.enable` | `true` | PPD-1, `balanced-performance` spelling. |
| `powerProfilesDaemon.patches.governor.enable` | `true` | PPD-2, governor before EPP. Makes ppd override another program's performance governor when it applies a profile. |

The ppd patches apply only to power-profiles-daemon 0.30. Any other
version fails evaluation with a message naming both options; set both to
`false` to use an unpatched ppd. The module does not enable ppd itself;
desktops such as Plasma usually do.

## Broker: `pt516.turbo` (`nixosModules.broker`)

| Option | Default | Meaning |
|---|---|---|
| `broker.enable` | `false` | Enable `turbo-broker.service`. |
| `broker.user` | none (required) | The normal user whose session uses the broker. Joins `turbo-broker-status`; `turbo-game.service` runs only for this user. Must equal `services.performance-policy.user` when the policy is enabled. |
| `broker.package` | `pkgs/turbo-broker` | The broker executable. |
| `broker.onBattery` | `"end-turbo"` | On battery while Turbo is on: `"end-turbo"` ends Turbo through ppd (balanced), once per Turbo entry; `"allow"` changes nothing; `"skip-addons"` releases lighting, idle timeout and the session hold and reapplies them on AC, leaving hardware Turbo. |
| `broker.profileAddons` | `[ ]` | Add-ons (`"performanceMode"`, `"lighting"`) for a plain `performance` profile outside GameMode and Turbo. |
| `games.enable` | `true` | Enable GameMode, its `powersave` governor setting and `turbo-game.service` as the game lifetime. |
| `games.performanceProfile` | `true` | Request the `performance` profile while a game runs; never Turbo fans; yields to the key and to other selections. |
| `games.addons.performanceMode` | `true` | Hold `performance-mode-app.service` while a game runs (needs the policy). |
| `games.addons.lighting` | `false` | Apply the Turbo lighting while a game runs. |
| `addons.performanceMode.enable` | `true` | Hold `performance-mode-turbo.service` while Turbo is on (needs the policy). |
| `addons.lighting.enable` | `true` | Apply the keyboard color while Turbo is on. |
| `addons.lighting.color` | `"255 32 0"` | R G B, each 0-255. |
| `addons.lighting.brightness` | `255` | 0-255. |
| `addons.lighting.stayOn` | `false` | Keep the color after Turbo ends. |
| `addons.lighting.idleTimeoutSeconds` | `null` | Keyboard idle timeout while lighting is applied: 0 = never, 1-255 s, `null` = unchanged. The original is restored on release. |
| `addons.lighting.kde.disableAccentSync` | `true` | Write `/etc/xdg/kdeglobals` with `DeviceLedsAccentColored=false` (immutable), so KDE's Kameleon does not overwrite the color at login. This affects every RGB device Kameleon supports, not only the keyboard. |
| `keys.predatorSense` | `"none"` | `"performance-mode"`: the PredatorSense key toggles the policy's manual hold (needs the broker and the policy). `"gui"` is reserved and fails evaluation. |

The broker reads `/etc/turbo-broker.json`
([interface](architecture.md#runtime-interface)) at start and restarts
when it changes. With the policy absent or disabled, the two `performanceMode`
add-ons have no effect and produce a warning; set them to `false`.

## Session policy: `services.performance-policy` (`nixosModules.policy`)

| Option | Default | Meaning |
|---|---|---|
| `enable` | `false` | Enable the policy and the `performance-mode` command. |
| `user` | none (required) | The normal user allowed to request it. Joins the policy group. |
| `preset` | `"custom"` | `"protect"` enables `protection.enable`; `"focused"` also `desktop.enable`. |
| `protection.enable` | `preset != "custom"` | Set `memory.low` on `performance.slice` (`appMiB`) and `session.slice` (`sessionMiB`) and raise their ancestors while held; stronger existing values are kept. |
| `protection.appMiB`, `protection.sessionMiB` | none (required with protection) | MiB values. |
| `desktop.enable` | `preset == "focused"` | Stop `desktop.units` while held. |
| `desktop.units` | `[ ]` | Desktop user units, e.g. the panel. Leave out compositor, audio and session infrastructure. |
| `userUnits`, `extraUserUnits` | `[ ]` | Background user units stopped while held; restarted only if they were running at entry. |
| `systemUnits` | `[ ]` | Background system units, same rule, in stop order. |
| `virtualMachines` | `[ ]` | List of `{ units = [ "name.service" or patterns ]; restore = true; }`. `restore = false` leaves restarting to another controller. |
| `soundDirectory` | `null` | Directory with `power-plug.oga` and `power-unplug.oga` played at entry and exit. |
| `disableXalia` | `false` | Default `PROTON_USE_XALIA=0` in `performance-mode run`. |
| `gameMode` | `true` | Enable GameMode with hooks holding `performance-mode-app.service`; the polkit group is then `gamemode`, otherwise `performance-policy`. |
| `agent.enable` | `false` | The session agent (`turbo-agent`). The broker module enables and wires it. |
| `agent.hardwareStateFile` | `"/run/performance-hardware/state.json"` | Hardware journal to observe. |
| `agent.hardwareSocket` | `null` | Socket for game-profile requests. |
| `agent.gameUnit` | `"performance-mode-app.service"` | Unit representing the game lifetime. |
| `agent.games` | `true` | Observe the game unit. |
| `agent.performanceMode` | `true` | Hold `performance-mode-turbo.service` while the journal reports Turbo. |
| `agent.profile` | `false` | Also hold it for a plain `performance` profile. |
| `agent.onBattery` | `"allow"` | Session side of the battery policy; the broker module sets it to the broker's value. |

Commands: `performance-mode on|off|status`, `performance-mode run CMD...`
(Steam: `performance-mode run %command%`), `turbo-agent status --json`,
`turbo-agent release performance-mode`.

## Users and groups

| Group | Added by | Grants |
|---|---|---|
| `turbo-broker-status` | `pt516.turbo.broker.user` | Read the broker journal, send game-profile requests. |
| `gamemode` (with `gameMode`) or `performance-policy` | `services.performance-policy.user` | Start and stop `performance-mode-system@<own uid>.service` through polkit. |

Membership takes effect at the next login. `turbo-broker on|off` and
`turbo-broker boot-reset` stay root-only.

## Examples

- Hardware only: [examples/minimal](../examples/minimal/configuration.nix).
- All three layers for user `alice`:
  [examples/broker-policy](../examples/broker-policy/configuration.nix).
- Broker without the policy: import `nixosModules.hardware` and
  `nixosModules.broker`, set `pt516.turbo.broker = { enable = true;
  user = "alice"; }` and set the two `performanceMode` add-ons to `false`.
- Policy on another machine: import only `nixosModules.policy`.
