# Upstream contributions

The one status table for changes this project carries or depends on.
Users update their pinned acer-pt516-nix release; they do not need to
assemble fixes from this table. Upstream acceptance, merge, release and
availability in the selected nixpkgs are separate milestones. A patch leaves
the bundle only when the selected source contains it or a verified
equivalent ([maintainers/README.md](maintainers/README.md)).

States: *not submitted* (prepared locally, nothing sent), then submitted,
in review, merged, released. No URL exists until a submission does.

| ID | Purpose | Destination | URL | Source revision | State | In bundle | Upstream release containing it | Adopted in acer-pt516-nix (commit or tag) |
|---|---|---|---|---|---|---|---|---|
| K-A | acer-wmi: stale platform-profile pointer fix, gaming-WMI reply validation, flagged Turbo LED firmware, PT516-52s quirk, Turbo key selects the Turbo profile, Turbo state retained across reboot (8 patches, 0001-0008) | Linux, platform-drivers-x86 (mailing list) | none | `prep/linux-a` `a5af31e646283709436b595beef6b0e8ebac0a7c` | not submitted | yes | none | initial commit |
| K-B | acer-wmi: `LEDS_CLASS_MULTICOLOR` dependency and zoned RGB keyboard backlight (2 patches, 0009-0010) | Linux, platform-drivers-x86 | none | `prep/linux-rgb` `64bd0e32a5ea1542c6e208dd80ae6695f601c0e1` (on top of K-A) | not submitted | yes | none | initial commit |
| K-C | acer-wmi: keyboard backlight idle timeout attribute (1 patch, 0011) | Linux, platform-drivers-x86 | none | `prep/linux-timeout` `3db5cf57becbc3c5ad79092d9bc1fd00e3f39381` (on top of K-A) | not submitted | yes | none | initial commit |
| PPD-1 | Recognize the kernel's `balanced-performance` platform profile spelling | power-profiles-daemon (GitLab merge request) | none | `prep/ppd-spelling` `16c0ad23a58aa57f0a8dd51045f6776831cbfc26` (based on ppd main); bundled as its 0.30 backport `prep/ppd-spelling-0.30` `3fe661e528ff977bcea17dd7e693069d60ab81c0` | not submitted | yes | none | initial commit |
| PPD-2 | Restore the powersave governor before writing EPP while intel_pstate is active | power-profiles-daemon (GitLab merge request) | none | `prep/ppd-governor` `1c6750eefe668a303743a3669f21c8a71f882aaa` | not submitted | yes | none | initial commit |
| NP-1 | NixOS thermald unit: `Restart=on-failure`, `SuccessExitStatus=2` like upstream thermald's unit | nixpkgs (GitHub pull request) | none | `1dcfdaa2acc0edc37ddf0b069a8a6dd0f4fe412e` | not submitted | equivalent settings in `modules/hardware/thermald.nix` | none | initial commit |
| NP-2 | nixpkgs `acer-wmi-battery`: CRC32 module compression, which the kernel's decompressor accepts | nixpkgs (GitHub pull request; a competing pull request, #570616, exists) | none | `4c323a68caa476d8e7b5527a9368d9d3e52b1e68` | not submitted | no (informational; this project does not use that package) | none | not applicable |
| NH-1 | nixos-hardware profile for the PT516-52s (GPU and laptop defaults) | nixos-hardware (GitHub pull request) | none | `fa2fb8c1d6ed1b7f3ba8f275ad1236add341ac20` | not submitted | no (import it yourself once merged) | none | not applicable |
| FACER-1 | One-line PT516-52s fix for the separate facer driver | acer-predator-turbo-and-rgb-keyboard-linux-module (GitHub pull request) | none | `1e26214759f4eb5453b7331e1a17541cb298ad12` | not submitted | no (informational; an alternative driver, not combined with this one) | none | not applicable |

## Notes per bundled entry

- **K-A, K-B, K-C:** bundled as one integrated series (`prep/linux-abc`
  `7cf5a7c3615952b30a08bb190d3d78a68a1d667a` on pdx86 `review-ilpo-next`
  `2622c23b7b640476a06d767b6ae63315ba5993a8`); there are no per-series
  switches. The patches carry `Assisted-by: LLM` and no `Signed-off-by`
  yet.
- **PPD-1:** the prepared main-branch commit does not apply to 0.30 unchanged (main
  moved the surrounding list), so the bundle carries a backport with the
  conflict resolved to the 0.30 form.
- **PPD-2:** the bundled patch is the `src/` part of the commit only; its
  tests remain in the author's source checkout and are not bundled here.
  No merge request has been submitted. Option to disable:
  `pt516.hardware.powerProfilesDaemon.patches.governor.enable = false`.
- **NP-1:** the module sets the two unit settings at `mkDefault`, so
  nixpkgs' own definition wins once it exists. The rendered unit is the same.

## Retired entries

None yet.
