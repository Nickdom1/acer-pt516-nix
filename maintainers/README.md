# Maintainer tools

Not needed to use the modules.

- `sync-upstream-patches.sh`: exports the kernel and ppd source commits
  into `kernel/acer-wmi/` and `patches/power-profiles-daemon/`; `--check`
  reports drift. Usage and refs are in its header. The local branches it
  reads (`prep/linux-abc`, `prep/ppd-spelling-0.30`, `prep/ppd-governor`)
  exist only in the author's checkouts and are not published.
- `test_sync_upstream_patches.py`: runs the script in a temporary copy and
  checks drift detection, byte-stable exports and candidate labelling. It
  skips when the checkouts are missing (`python3
  maintainers/test_sync_upstream_patches.py`).
- `kernel-tests/`: compile actual driver functions against mocked firmware.
  `ACER_WMI_SOURCE` selects the driver file (default: a sibling
  `linux-pdx86` checkout); `nix build .#checks.x86_64-linux.acer-wmi-harness`
  runs them on this repository's patched file.
- `compare-module.py OLD NEW`: compares two module builds section by
  section and accepts only the compiler's `-frandom-seed` difference (see
  its docstring).

## Release process

## Candidates and releases

A candidate is a commit with a lightweight tag `candidate-YYYY-MM-DD`. A
release is a commit with an annotated tag `vYYYY.MM.DD[.N]`, made only after
the evidence for its changes exists. Tags are never moved or deleted, so
older configurations that pin them stay reproducible. The
tagged `kernel/acer-wmi/SOURCE`, `kernel/acer-wmi/BASE` and
`patches/power-profiles-daemon/SOURCE` record every source commit and hash.

Consumers pin a tag or commit (`rev`) and update it in a reviewed commit of
their own. An upstream review or a new branch head changes no consumer
until then. A script may propose an update; it never adopts one.

### When an upstream reviewer asks for changes

1. Edit the source commit in its own repository (kernel series branch,
   ppd branch). Never edit an exported patch.
2. Kernel: integrate the series again (`prep/linux-abc`). If the maintainer
   requires a new base, the export re-vendors `base/acer-wmi.c` from it.
   ppd spelling: update the 0.30 backport commit as well.
3. Export: `LINUX_PDX86=... POWER_PROFILES_DAEMON=...
   maintainers/sync-upstream-patches.sh`, then the same with `--check`.
4. Review `git diff kernel/ patches/`.
5. Verify: `nix flake check` (harnesses, module composition, VM test), the
   module build for the reference kernel and for 6.18.52, the ppd build.
   Compare module payloads with an earlier build section by section: the
   compiler records an output-path-derived `-frandom-seed` in the debug
   information, so whole-file hashes differ whenever the derivation does
   (`maintainers/compare-module.py`).
6. Hardware run scoped to the changed behavior; record it in
   `UPSTREAM.md` and `CHANGELOG.md`.
7. Commit and tag a candidate; tag a release once the evidence is in.

### Retiring a patch

A patch leaves the bundle only when the consumer's selected source contains
the fix or a verified equivalent.

- Kernel: when A is applied to a pdx86 branch, re-vendor `base/acer-wmi.c`
  from that commit and export B and C rebased onto it; A's code then
  arrives through the base, so the driver is unchanged and A stays an
  active entry. If the applied A differs from the bundled one, the harness
  and a hardware run scoped to the difference count as the verification.
  When the consumer's selected kernel release contains A, B and C, the
  hardware module stops building `acer_wmi_pt516` for that kernel version
  (gated on `kernel.version`), stops blacklisting `acer_wmi`, and moves the
  modprobe option to `acer_wmi` if upstream keeps it; only then are the
  entries retired. Test the upstream-only path on hardware before the
  release that removes the patches.
- ppd: the module maps exact ppd versions to verified patch sets and fails
  evaluation for an unknown version. When a ppd release contains PPD-1 or
  PPD-2, add its version with the remaining patches (possibly none) after
  testing it. A patch applied twice fails the build loudly; it is never
  dropped silently.
- thermald: remove the two unit settings when the reference nixpkgs
  contains NP-1.

Move retired entries to "Retired entries" in `UPSTREAM.md` with the release
that dropped them. Keep patch count and feature completeness as separate
measures: the broker and session policy stay useful after the hardware
patches retire.
