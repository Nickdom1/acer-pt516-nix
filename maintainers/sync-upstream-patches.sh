#!/usr/bin/env bash
# Export the kernel and power-profiles-daemon source commits that this
# repository ships; --check reports drift without writing anything.
#
# Never edit the exported files. Edit the source commits in their own
# checkouts, then run this script again:
#
#   LINUX_PDX86=<pdx86 checkout> POWER_PROFILES_DAEMON=<ppd checkout> \
#     maintainers/sync-upstream-patches.sh [--check]
#
# Kernel: the integrated review series ACER_WMI_BRANCH (default
# prep/linux-abc = series A, B and C) on top of ACER_WMI_BASE (default pdx86
# review-ilpo-next 2622c23b). Writes kernel/acer-wmi/patches/*.patch,
# README.md, SOURCE, BASE and base/acer-wmi.c (the driver at the base
# commit; vendored because that commit is reachable only from a moving
# review branch). modules/hardware/acer-wmi.nix refuses to evaluate when
# SOURCE's base hash does not match base/acer-wmi.c.
#
# ppd: two src-only exports for the 0.30 release, the spelling backport
# PPD_SPELLING_REF (default prep/ppd-spelling-0.30, a commit on tag 0.30) and
# the governor fix PPD_GOVERNOR_REF (default prep/ppd-governor, limited to
# src/ppd-driver-intel-pstate.c, which is the same blob in 0.30 and main).
# Writes patches/power-profiles-daemon/{0001,0002}-*.patch, README.md, SOURCE.
#
# The defaults are the "release" export. Any other kernel or ppd ref makes a
# "candidate" export, and README.md and SOURCE say so.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

tree=${LINUX_PDX86:-}
ppd_tree=${POWER_PROFILES_DAEMON:-}
[[ -n $tree && -n $ppd_tree ]] || {
  echo "set LINUX_PDX86 and POWER_PROFILES_DAEMON to the kernel and ppd checkouts" >&2; exit 2;
}
default_branch=prep/linux-abc
default_base=2622c23b7b640476a06d767b6ae63315ba5993a8
default_base_branch=review-ilpo-next
default_ppd_spelling=prep/ppd-spelling-0.30
default_ppd_governor=prep/ppd-governor
default_ppd_release=0.30
branch=${ACER_WMI_BRANCH:-$default_branch}
base=${ACER_WMI_BASE:-$default_base}
base_branch=${ACER_WMI_BASE_BRANCH:-$default_base_branch}
ppd_spelling=${PPD_SPELLING_REF:-$default_ppd_spelling}
ppd_governor=${PPD_GOVERNOR_REF:-$default_ppd_governor}
ppd_release=$default_ppd_release
if [[ $branch == "$default_branch" && $base == "$default_base" ]]; then
  label=release
else
  label=candidate
fi
if [[ $ppd_spelling == "$default_ppd_spelling" && $ppd_governor == "$default_ppd_governor" ]]; then
  ppd_label=release
else
  ppd_label=candidate
fi
out=kernel/acer-wmi
ppd_out=patches/power-profiles-daemon
driver=drivers/platform/x86/acer-wmi.c
check=${1:-}
[[ $# -le 1 && ( -z $check || $check == --check ) ]] || {
  echo "usage: LINUX_PDX86=<dir> POWER_PROFILES_DAEMON=<dir> [ACER_WMI_BRANCH=<ref> ACER_WMI_BASE=<ref>] [PPD_SPELLING_REF=<ref> PPD_GOVERNOR_REF=<ref>] $0 [--check]" >&2; exit 2;
}

[[ -d $tree/.git || -f $tree/.git ]] || { echo "kernel tree not found at $tree" >&2; exit 1; }
[[ -d $ppd_tree/.git || -f $ppd_tree/.git ]] || { echo "ppd tree not found at $ppd_tree" >&2; exit 1; }
git -C "$tree" rev-parse --verify -q "$branch^{commit}" >/dev/null || { echo "branch $branch missing in $tree" >&2; exit 1; }
git -C "$tree" rev-parse --verify -q "$base^{commit}" >/dev/null || { echo "base $base missing in $tree" >&2; exit 1; }
# The module applies the export to acer-wmi.c at the base, so the range must
# be exactly the commits on top of it.
git -C "$tree" merge-base --is-ancestor "$base" "$branch" || { echo "base $base is not an ancestor of $branch" >&2; exit 1; }
for ref in "$ppd_spelling" "$ppd_governor" "$ppd_release"; do
  git -C "$ppd_tree" rev-parse --verify -q "$ref^{commit}" >/dev/null || { echo "ref $ref missing in $ppd_tree" >&2; exit 1; }
done
# The spelling export is a backport: it must sit directly on the release tag.
[[ $(git -C "$ppd_tree" rev-parse "$ppd_spelling^^{commit}") == $(git -C "$ppd_tree" rev-parse "$ppd_release^{commit}") ]] || {
  echo "$ppd_spelling is not a single commit on tag $ppd_release" >&2; exit 1;
}
base_rev=$(git -C "$tree" rev-parse "$base^{commit}")
head_rev=$(git -C "$tree" rev-parse "$branch^{commit}")
base_date=$(git -C "$tree" log -1 --format=%cs "$base_rev")
spelling_rev=$(git -C "$ppd_tree" rev-parse "$ppd_spelling^{commit}")
governor_rev=$(git -C "$ppd_tree" rev-parse "$ppd_governor^{commit}")
release_rev=$(git -C "$ppd_tree" rev-parse "$ppd_release^{commit}")
spelling_upstream=$(git -C "$ppd_tree" log -1 --format=%B "$spelling_rev" \
  | sed -n 's/^(cherry picked from commit \([0-9a-f]\{40\}\))$/\1/p')
# Name a symbolic base and its commit; print a commit id base only once.
if [[ $base == "$base_rev" ]]; then
  base_desc=$base_rev base_md=$base_rev
else
  base_desc="$base = $base_rev" base_md="\`$base\` = $base_rev"
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/kernel/base" "$tmp/ppd"
# Pin the abbreviation in the patches' "index" lines. Git's default
# (core.abbrev=auto) lengthens it as a clone grows (9 hex digits when the
# first export was written, 12 by 2026-10-06), which rewrites every patch,
# and so the module derivation, without a source change. Git still lengthens
# an abbreviation that would be ambiguous in the clone; --check ignores
# kernel index lines, so only a re-export shows that. The signature line
# ("-- " and the Git version) changes with a Git upgrade, and --check
# reports that.
git -C "$tree" -c core.abbrev=9 format-patch -q -o "$tmp/kernel" "$base_rev..$head_rev"
git -C "$tree" show "$base_rev:$driver" > "$tmp/kernel/base/acer-wmi.c"
base_sha=$(sha256sum < "$tmp/kernel/base/acer-wmi.c" | cut -c1-64)
head_sha=$(git -C "$tree" show "$head_rev:$driver" | sha256sum | cut -c1-64)
git -C "$ppd_tree" -c core.abbrev=9 format-patch -1 --stdout "$spelling_rev" -- src/ \
  > "$tmp/ppd/0001-balanced-performance-spelling.patch"
git -C "$ppd_tree" -c core.abbrev=9 format-patch -1 --stdout "$governor_rev" -- src/ppd-driver-intel-pstate.c \
  > "$tmp/ppd/0002-intel-pstate-governor.patch"
# The public bundle carries the project's address in the author header; the
# source commits keep the author's own address for upstream submission.
public_mail=no-reply@detello.com
sed -i -E "2s/^(From: .*) <[^>]+>\$/\\1 <$public_mail>/" "$tmp"/kernel/*.patch "$tmp"/ppd/*.patch

if [[ $label == release ]]; then
  cat > "$tmp/kernel/README.md" <<EOF
# acer-wmi export for the Predator PT516-52s (release range)

Generated by \`maintainers/sync-upstream-patches.sh\`; do not edit these files.
The source commits are the integrated review series \`$branch\` in a
platform-drivers-x86 checkout. Edit the series commits, integrate them
again, then export.
Range \`$base..$branch\`:
base $base_md,
head $head_rev.

[SOURCE](SOURCE) identifies this export and [BASE](BASE) the vendored
driver file \`base/acer-wmi.c\`. \`pkgs/acer-wmi-pt516\` applies the
\`$driver\` hunks of the numbered patches to that file, in
order, to build the out-of-tree module \`acer_wmi_pt516\`. The other hunks
(Kconfig, ABI documentation) are kept for fidelity.

These are pre-submission drafts, pending upstream acceptance. They carry
\`Assisted-by: LLM\` and no \`Signed-off-by\`, and their author header uses the
project address \`no-reply@detello.com\`; the author adds his sign-off and address
only when submitting. Each patch carries the license of the upstream file
it modifies, per that file's SPDX header. The vendored driver is
GPL-2.0-or-later; the Kconfig file modified by patch 0009 is GPL-2.0-only.
These descriptions do not change any upstream license.
[UPSTREAM.md](../../UPSTREAM.md) tracks each series' state.
EOF
else
  cat > "$tmp/kernel/README.md" <<EOF
# acer-wmi candidate export for the Predator PT516-52s

Generated by \`maintainers/sync-upstream-patches.sh\` with \`ACER_WMI_BRANCH\`
and \`ACER_WMI_BASE\`; do not edit these files. This candidate is not the
release export: change its source commits and export again.
Range \`$base..$branch\`:
base $base_md,
head $head_rev.

[SOURCE](SOURCE) identifies it and [BASE](BASE) the vendored driver file
\`base/acer-wmi.c\` at that base. Exporting a candidate neither installs nor
tests it. [UPSTREAM.md](../../UPSTREAM.md) tracks the submission series.
EOF
fi
{
  echo "export: $label"
  echo "repo: https://git.kernel.org/pub/scm/linux/kernel/git/pdx86/platform-drivers-x86.git"
  echo "branch: $branch"
  echo "base: $base_desc ($base_date)"
  echo "head: $head_rev"
  echo "acer-wmi.c at base: sha256 $base_sha"
  echo "acer-wmi.c at head: sha256 $head_sha"
} > "$tmp/kernel/SOURCE"
{
  echo "repo: https://git.kernel.org/pub/scm/linux/kernel/git/pdx86/platform-drivers-x86.git"
  echo "branch: $base_branch"
  echo "commit: $base_rev"
  echo "date: $base_date"
  echo "path: $driver"
  echo "plain: https://git.kernel.org/pub/scm/linux/kernel/git/pdx86/platform-drivers-x86.git/plain/$driver?id=$base_rev"
  echo "sha256: $base_sha"
  echo "license: GPL-2.0-or-later (SPDX line in the file)"
  echo "vendored: the commit is reachable only from a moving review branch; the plain URL is a cross-check that may stop resolving"
} > "$tmp/kernel/BASE"

cat > "$tmp/ppd/README.md" <<EOF
# power-profiles-daemon patches for release $ppd_release

Generated by \`maintainers/sync-upstream-patches.sh\` ($ppd_label export); do
not edit these files. Each patch is the \`src/\` part of one commit, so it
applies to the $ppd_release release tarball that nixpkgs builds.
\`modules/hardware/power-profiles-daemon.nix\` applies them only to
power-profiles-daemon $ppd_release and fails evaluation for any other version.

- \`0001-balanced-performance-spelling.patch\`: recognise the kernel's
  \`balanced-performance\` platform profile spelling. A backport to
  $ppd_release of the upstream merge request commit; the main branch moved the
  surrounding list, so the commit does not apply to $ppd_release unchanged.
- \`0002-intel-pstate-governor.patch\`: restore the powersave governor
  before writing EPP while intel_pstate is active.

Both are pending upstream acceptance. Each patch carries the license of
the upstream file it modifies, per that file's SPDX header or existing
license notice where no SPDX identifier is present. The affected ppd C
files state GPL-3.0 without "or later". These descriptions do not change
any upstream license. [SOURCE](SOURCE) identifies the commits and
[UPSTREAM.md](../../UPSTREAM.md) tracks their state.
EOF
{
  echo "export: $ppd_label"
  echo "repo: https://gitlab.freedesktop.org/upower/power-profiles-daemon.git"
  echo "release: $ppd_release = $release_rev"
  echo "0001-balanced-performance-spelling.patch: $ppd_spelling = $spelling_rev (src/; backport of ${spelling_upstream:-an unrecorded upstream commit}) sha256 $(sha256sum < "$tmp/ppd/0001-balanced-performance-spelling.patch" | cut -c1-64)"
  echo "0002-intel-pstate-governor.patch: $ppd_governor = $governor_rev (src/ppd-driver-intel-pstate.c) sha256 $(sha256sum < "$tmp/ppd/0002-intel-pstate-governor.patch" | cut -c1-64)"
} > "$tmp/ppd/SOURCE"

strip_volatile() {
  sed -e '/^From [0-9a-f]\{40\}/d' -e '/^Date: /d' -e '/^index [0-9a-f]*\.\./d' "$1"
}

if [[ $check == --check ]]; then
  drift=0
  if ! diff -q "$tmp/kernel/README.md" "$out/README.md" >/dev/null 2>&1; then
    echo "drift: kernel export README differs ($label export of $branch on $base expected)"; drift=1
  fi
  for f in SOURCE BASE base/acer-wmi.c; do
    if ! diff -q "$tmp/kernel/$f" "$out/$f" >/dev/null 2>&1; then
      echo "drift: $out/$f differs from $tree ($branch on $base)"; drift=1
    fi
  done
  for f in "$tmp"/kernel/*.patch; do
    n=$(basename "$f" | cut -c1-4)
    matches=("$out"/patches/"$n"-*.patch)
    cur=${matches[0]}
    if [[ ! -f $cur ]] || ! diff -q <(strip_volatile "$f") <(strip_volatile "$cur") >/dev/null; then
      echo "drift: patch $n differs from $tree:$branch"; drift=1
    fi
  done
  # Also detect removed/renamed exports; otherwise a stale patch would still
  # be compiled into the module even though every source patch matched.
  for cur in "$out"/patches/*.patch; do
    [[ -f $cur ]] || continue
    if [[ ! -f $tmp/kernel/$(basename "$cur") ]]; then
      echo "drift: stale export $cur"; drift=1
    fi
  done
  for f in "$tmp"/ppd/*; do
    if ! diff -q "$f" "$ppd_out/$(basename "$f")" >/dev/null 2>&1; then
      echo "drift: $ppd_out/$(basename "$f") differs from $ppd_tree ($ppd_label export)"; drift=1
    fi
  done
  for cur in "$ppd_out"/*.patch; do
    [[ -f $cur ]] || continue
    if [[ ! -f $tmp/ppd/$(basename "$cur") ]]; then
      echo "drift: stale export $cur"; drift=1
    fi
  done
  if (( drift )); then exit 1; fi
  echo "Kernel ($label: $branch on $base) and ppd ($ppd_label) exports match their source commits"
  exit 0
fi

mkdir -p "$out/patches" "$out/base" "$ppd_out"
rm -f "$out"/patches/*.patch "$ppd_out"/*.patch
cp "$tmp"/kernel/*.patch "$out/patches/"
cp "$tmp/kernel/README.md" "$tmp/kernel/SOURCE" "$tmp/kernel/BASE" "$out/"
cp "$tmp/kernel/base/acer-wmi.c" "$out/base/acer-wmi.c"
cp "$tmp"/ppd/* "$ppd_out/"
printf '%s\n' "$out"/patches/*.patch "$ppd_out"/*.patch | sed 's|.*/||'
echo "wrote $out ($label export) and $ppd_out ($ppd_label export)"
