#!/usr/bin/env python3
"""Check patch export drift in an isolated copy, never changing the real repos.

The kernel and ppd checkouts are read-only inputs. LINUX_PDX86 and
POWER_PROFILES_DAEMON select them (default: sibling checkouts named
linux-pdx86 and power-profiles-daemon next to this repository); the tests
skip when they are missing. The repository's own export may be the release
export or a candidate; kernel/acer-wmi/SOURCE names the range, and the drift
tests check against that range.
"""
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = "maintainers/sync-upstream-patches.sh"
SCRIPT_TEXT = (REPO / SCRIPT).read_text()
KERNEL = "kernel/acer-wmi"
PPD = "patches/power-profiles-daemon"


def script_default(name):
    """The script's default_<name>=... assignment; the tests follow the script, not a copy."""
    for line in SCRIPT_TEXT.splitlines():
        if line.startswith(f"default_{name}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"default_{name} not found in the export script")


RELEASE_BRANCH = script_default("branch")
RELEASE_BASE = script_default("base")


class SyncUpstreamPatchesTests(unittest.TestCase):
    def setUp(self):
        self.kernel = Path(os.environ.get("LINUX_PDX86", str(REPO.parent / "linux-pdx86")))
        ppd = Path(os.environ.get("POWER_PROFILES_DAEMON", str(REPO.parent / "power-profiles-daemon")))
        for checkout in (self.kernel, ppd):
            if not (checkout / ".git").exists():
                self.skipTest(f"required read-only source checkout is missing: {checkout}")
        if not shutil.which("bash") or not shutil.which("git"):
            self.skipTest("bash and git are required for export fixture tests")
        if not list((REPO / KERNEL / "patches").glob("0010-*.patch")):
            self.skipTest("the export must include patch 0010 to exercise this regression")
        self.env = dict(os.environ, LINUX_PDX86=str(self.kernel), POWER_PROFILES_DAEMON=str(ppd))
        for name in ("ACER_WMI_BRANCH", "ACER_WMI_BASE"):
            self.env.pop(name, None)
        self.export_env = self.repository_export_range()
        for ref in self.export_env.values():
            if subprocess.run(["git", "-C", str(self.kernel), "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"],
                              stdout=subprocess.DEVNULL, check=False).returncode:
                self.skipTest(f"the repository's export was made from {ref}, which is not in {self.kernel}")

    @staticmethod
    def repository_export_range():
        """Overrides that reproduce the repository's own export, from its SOURCE."""
        fields = {}
        for line in (REPO / KERNEL / "SOURCE").read_text().splitlines():
            key, _, value = line.partition(": ")
            fields[key] = value
        if fields.get("export", "release") == "release":
            return {}
        # "base: for-next = <commit> (date)" or "base: <commit> (date)"
        base = fields["base"].rsplit(" (", 1)[0].partition(" = ")[0]
        return {"ACER_WMI_BRANCH": fields["branch"], "ACER_WMI_BASE": base}

    @contextlib.contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory(prefix="predator-export-fixture-") as directory:
            fixture = Path(directory)
            (fixture / "maintainers").mkdir()
            shutil.copyfile(REPO / SCRIPT, fixture / SCRIPT)
            shutil.copytree(REPO / KERNEL, fixture / KERNEL)
            shutil.copytree(REPO / PPD, fixture / PPD)
            yield fixture

    def run_script(self, fixture, *args, **overrides):
        return subprocess.run(
            ["bash", str(fixture / SCRIPT), *args],
            env=dict(self.env, **overrides), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )

    def rev(self, ref):
        return subprocess.run(["git", "-C", str(self.kernel), "rev-parse", f"{ref}^{{commit}}"],
                              text=True, capture_output=True, check=True).stdout.strip()

    @staticmethod
    def snapshot(kernel):
        names = ["patches/" + p.name for p in (kernel / "patches").glob("*.patch")]
        names += ["README.md", "BASE", "base/acer-wmi.c"]
        return {name: (kernel / name).read_bytes() for name in names}

    def assert_export_identical(self, exports, expected):
        # Byte-for-byte, including the abbreviated "index" lines: a changed
        # patch file changes the module derivation even when --check passes.
        actual = self.snapshot(exports)
        self.assertEqual(sorted(actual), sorted(expected))
        for name in expected:
            self.assertEqual(actual[name], expected[name], name)

    def test_stale_renamed_and_changed_exports_after_patch_nine(self):
        with self.fixture() as fixture:
            kernel = fixture / KERNEL
            exports = kernel / "patches"
            expected_count = len(list(exports.glob("*.patch")))

            def run(*args):
                return self.run_script(fixture, *args, **self.export_env)

            with self.subTest("clean exports match source"):
                result = run("--check")
                self.assertEqual(result.returncode, 0, result.stdout)

            stale = exports / "0010-stale-fixture.patch"
            stale.write_text("obsolete development export\n")
            with self.subTest("detect stale export after patch nine"):
                result = run("--check")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("stale export kernel/acer-wmi/patches/0010-stale-fixture.patch", result.stdout)

            with self.subTest("sync removes stale export and preserves other files"):
                result = run()
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertFalse(stale.exists())
                self.assertEqual(len(list(exports.glob("*.patch"))), expected_count)
                self.assertTrue((kernel / "README.md").exists())

            with self.subTest("regenerated fixture matches source"):
                result = run("--check")
                self.assertEqual(result.returncode, 0, result.stdout)

            (kernel / "README.md").write_text("obsolete patch count and acceptance claim\n")
            with self.subTest("detect and replace stale export guidance"):
                result = run("--check")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("kernel export README differs", result.stdout)
                result = run()
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("UPSTREAM.md", (kernel / "README.md").read_text())

            original = next(exports.glob("0010-*.patch"))
            renamed = original.with_name("0010-old-export-name.patch")
            original.rename(renamed)
            with self.subTest("detect renamed export despite identical contents"):
                result = run("--check")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("stale export", result.stdout)

            result = run()
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertFalse(renamed.exists())
            original = next(exports.glob("0010-*.patch"))
            original.write_text(original.read_text() + "changed fixture payload\n")
            with self.subTest("detect changed export payload"):
                result = run("--check")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("patch 0010 differs", result.stdout)

            with self.subTest("re-export repairs all drift"):
                result = run()
                self.assertEqual(result.returncode, 0, result.stdout)
                result = run("--check")
                self.assertEqual(result.returncode, 0, result.stdout)

    def test_export_is_byte_identical_despite_abbreviation_config(self):
        # A grown clone or a user's core.abbrev must not rewrite the "index"
        # lines; the script pins them (see its comment).
        real = REPO / KERNEL
        with self.fixture() as fixture:
            kernel = fixture / KERNEL
            for patch in (kernel / "patches").glob("*.patch"):
                patch.unlink()
            for patch in (fixture / PPD).glob("*.patch"):
                patch.unlink()
            result = self.run_script(fixture, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.abbrev",
                                     GIT_CONFIG_VALUE_0="14", **self.export_env)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assert_export_identical(kernel, self.snapshot(real))
            for name in ("0001-balanced-performance-spelling.patch", "0002-intel-pstate-governor.patch", "SOURCE"):
                self.assertEqual((fixture / PPD / name).read_bytes(), (REPO / PPD / name).read_bytes(), name)
            source = (kernel / "SOURCE").read_text()
            label = "candidate" if self.export_env else "release"
            branch = self.export_env.get("ACER_WMI_BRANCH", RELEASE_BRANCH)
            self.assertTrue(source.startswith(f"export: {label}\n"), source)
            self.assertIn(f"branch: {branch}\n", source)
            self.assertIn(f"head: {self.rev(branch)}\n", source)

    def test_candidate_export_identifies_its_own_source(self):
        branch, base = RELEASE_BRANCH, f"{RELEASE_BRANCH}~2"
        head_rev, base_rev = self.rev(branch), self.rev(base)
        with self.fixture() as fixture:
            kernel = fixture / KERNEL
            exports = kernel / "patches"
            candidate = {"ACER_WMI_BRANCH": branch, "ACER_WMI_BASE": base}
            # Start from a release export whatever the repository holds.
            result = self.run_script(fixture)
            self.assertEqual(result.returncode, 0, result.stdout)
            release = self.snapshot(kernel)

            with self.subTest("explicit default values still mean the release export"):
                result = self.run_script(fixture, "--check", ACER_WMI_BRANCH=RELEASE_BRANCH,
                                         ACER_WMI_BASE=RELEASE_BASE)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("release", result.stdout)

            with self.subTest("release export is drift for a candidate range"):
                result = self.run_script(fixture, "--check", **candidate)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("kernel export README differs (candidate export", result.stdout)
                self.assertIn("stale export", result.stdout)

            with self.subTest("a base that is not an ancestor is refused without writing"):
                before = sorted(p.name for p in exports.iterdir())
                result = self.run_script(fixture, ACER_WMI_BRANCH=RELEASE_BASE, ACER_WMI_BASE=branch)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("is not an ancestor", result.stdout)
                self.assertEqual(sorted(p.name for p in exports.iterdir()), before)
                self.assert_export_identical(kernel, release)

            with self.subTest("candidate export names its branch, base and head"):
                result = self.run_script(fixture, **candidate)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(len(list(exports.glob("*.patch"))), 2)
                readme = (kernel / "README.md").read_text()
                self.assertIn("# acer-wmi candidate export", readme)
                self.assertIn(f"Range `{base}..{branch}`", readme)
                self.assertIn(f"base `{base}` = {base_rev}", readme)
                self.assertIn(f"head {head_rev}", readme)
                for release_wording in ("(release range)", "The source commits are", "pre-submission drafts"):
                    self.assertNotIn(release_wording, readme)
                source = (kernel / "SOURCE").read_text()
                self.assertTrue(source.startswith("export: candidate\n"), source)
                self.assertIn(f"branch: {branch}\n", source)
                self.assertIn(f"base: {base} = {base_rev} (", source)
                self.assertIn(f"head: {head_rev}\n", source)
                self.assertIn("acer-wmi.c at base: sha256 ", source)
                self.assertIn("acer-wmi.c at head: sha256 ", source)
                self.assertIn(f"commit: {base_rev}\n", (kernel / "BASE").read_text())
                base_file = subprocess.run(
                    ["git", "-C", str(self.kernel), "show", f"{base_rev}:drivers/platform/x86/acer-wmi.c"],
                    capture_output=True, check=True,
                ).stdout
                self.assertEqual((kernel / "base/acer-wmi.c").read_bytes(), base_file)
                for name in ("0001-balanced-performance-spelling.patch", "0002-intel-pstate-governor.patch"):
                    self.assertEqual((fixture / PPD / name).read_bytes(), (REPO / PPD / name).read_bytes())

            with self.subTest("candidate check passes; the default check flags it"):
                result = self.run_script(fixture, "--check", **candidate)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("candidate", result.stdout)
                result = self.run_script(fixture, "--check")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("kernel export README differs (release export", result.stdout)

            with self.subTest("a commit id base is named once"):
                result = self.run_script(fixture, ACER_WMI_BRANCH=branch, ACER_WMI_BASE=base_rev)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn(f"\nbase {base_rev},\n", (kernel / "README.md").read_text())
                self.assertIn(f"\nbase: {base_rev} (", (kernel / "SOURCE").read_text())

            with self.subTest("default export restores the release export exactly"):
                result = self.run_script(fixture)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assert_export_identical(kernel, release)
                self.assertTrue((kernel / "SOURCE").read_text().startswith("export: release\n"))


    def test_ppd_exports(self):
        with self.fixture() as fixture:
            ppd = fixture / PPD
            governor = ppd / "0002-intel-pstate-governor.patch"
            with self.subTest("changed ppd export is drift"):
                governor.write_text(governor.read_text() + "changed fixture payload\n")
                result = self.run_script(fixture, "--check", **self.export_env)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("0002-intel-pstate-governor.patch differs", result.stdout)
            with self.subTest("stale ppd export is drift"):
                (ppd / "0003-stale-fixture.patch").write_text("obsolete\n")
                result = self.run_script(fixture, "--check", **self.export_env)
                self.assertIn("stale export patches/power-profiles-daemon/0003-stale-fixture.patch", result.stdout)
            with self.subTest("re-export repairs ppd drift"):
                result = self.run_script(fixture, **self.export_env)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertFalse((ppd / "0003-stale-fixture.patch").exists())
                self.assertEqual(governor.read_bytes(), (REPO / PPD / governor.name).read_bytes())
            with self.subTest("a spelling ref that is not one commit on the release tag is refused"):
                before = {p.name: p.read_bytes() for p in ppd.iterdir()}
                result = self.run_script(fixture, PPD_SPELLING_REF=script_default("ppd_governor"), **self.export_env)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("is not a single commit on tag", result.stdout)
                self.assertEqual({p.name: p.read_bytes() for p in ppd.iterdir()}, before)
            with self.subTest("missing checkout locations are refused"):
                env = {k: v for k, v in self.env.items() if k != "POWER_PROFILES_DAEMON"}
                result = subprocess.run(["bash", str(fixture / SCRIPT), "--check"], env=env, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
                self.assertEqual(result.returncode, 2, result.stdout)

if __name__ == "__main__":
    unittest.main()
