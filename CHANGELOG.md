# Changelog

## Initial commit, 2026-10-06

- Hardware layer: `acer_wmi_pt516` from pdx86 `review-ilpo-next` `2622c23b`
  (vendored) plus the integrated series `prep/linux-abc` `7cf5a7c3` (K-A,
  K-B, K-C, 11 patches); stock `acer_wmi` blacklisted; Turbo key toggle;
  once-per-boot reset; thermald with upstream's unit restart policy;
  power-profiles-daemon 0.30 with PPD-1 (backport `3fe661e5`) and PPD-2
  (`1c6750ee`).
- Broker layer: `turbo-broker` and its module; `broker.user`,
  `addons.lighting.kde.disableAccentSync`; off by default; battery policy
  `end-turbo` by default; peripheral batteries (HID) do not count as line
  power.
- Session policy: `services.performance-policy` and `turbo-agent`.
- Checks: 83 unit tests, the actual-C driver harness, 40 module-composition
  checks, a NixOS VM test of the policy.
