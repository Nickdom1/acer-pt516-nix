"""Recovery/ownership regressions, with filesystem-backed fake LEDs and fans."""
import contextlib
import copy
import io
import importlib.util
from pathlib import Path
import signal
import socket
import sys
import time
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'broker', Path(__file__).resolve().parents[1] / 'pkgs/turbo-broker/turbo-broker.py'
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
CONFIG = {
    'user': 'alice', 'onBattery': 'allow',
    # Most recovery cases explicitly opt into profile-only effects; trigger
    # selection itself is tested separately against the conservative default.
    'profileAddons': ['lighting'],
    'games': {'enable': True, 'addons': {'lighting': False}},
    'addons': {
        'lighting': {'enable': True, 'color': '255 32 0', 'brightness': 255, 'stayOn': False},
    },
}


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.zone = self.root / 'left'
        self.zone.mkdir()
        for name, value in {'multi_intensity': '61 174 233', 'brightness': '128', 'max_brightness': '255'}.items():
            (self.zone / name).write_text(value)
        self.profile = self.root / 'platform_profile'
        self.profile.write_text('balanced')
        self.pwm = self.root / 'pwm1_enable'
        self.pwm.write_text('2')
        self.calls = []
        self.addCleanup(patch.stopall)
        patch.object(m, 'zones', return_value=[self.zone]).start()
        patch.object(m, 'fans', return_value=[self.pwm]).start()
        patch.object(m, 'PROFILE', self.profile).start()
        self.cfg = copy.deepcopy(CONFIG)
        self.broker = m.Broker(self.cfg, self.root)


    def assert_restored(self):
        self.assertEqual(m.read(self.zone / 'multi_intensity'), '61 174 233')
        self.assertEqual(m.read(self.zone / 'brightness'), '128')
        self.assertEqual(self.broker.state['saved'], {})

    def test_zero_brightness_with_colour_is_snapshotted_and_restored_as_reported(self):
        # The former bug-7 guard recorded such a zone as lit at max_brightness.
        m.write(self.zone / 'brightness', '0')
        self.broker.reconcile('performance', False)
        self.assertEqual(self.broker.state['saved']['lighting'][str(self.zone)],
                         {'color': '61 174 233', 'brightness': '0'})
        self.assertEqual(m.read(self.zone / 'brightness'), '255')
        self.broker.reconcile('balanced', False)
        self.assertEqual(m.read(self.zone / 'multi_intensity'), '61 174 233')
        self.assertEqual(m.read(self.zone / 'brightness'), '0')
        self.assertFalse(hasattr(m, 'lit_brightness'))

    def test_plain_profile_defaults_to_no_addons_or_desktop_hold(self):
        self.cfg.pop('profileAddons')
        self.broker.reconcile('performance', False)
        self.assert_restored()
        self.assertEqual(self.broker.state['mode'], 'profile')
        self.broker.reconcile('performance', False, max_fans=True)

    def test_profile_opt_in_respects_global_enablement_and_game_subset(self):
        self.cfg['profileAddons'] = ['lighting']
        self.broker.reconcile('performance', False)
        self.assertEqual(self.broker.state['applied'], ['lighting'])
        self.broker.reconcile('performance', False, game=True)
        self.assert_restored()

    def test_removing_profile_opt_in_on_restart_restores_owned_effects(self):
        self.broker.reconcile('performance', False)
        self.cfg['profileAddons'] = []
        self.broker = m.Broker(self.cfg, self.root)
        self.broker.reconcile('performance', False)
        self.assert_restored()

    def test_restore_journal_is_not_world_readable(self):
        self.broker.reconcile('performance', False)
        self.assertEqual(self.broker.path.stat().st_mode & 0o777, 0o640)


    def test_restart_does_not_reapply_or_overwrite_snapshot(self):
        self.broker.reconcile('performance', False)
        saved = copy.deepcopy(self.broker.state['saved'])
        restarted = m.Broker(self.cfg, self.root)
        with patch.object(restarted, 'apply', side_effect=AssertionError('reapplied')):
            restarted.reconcile('performance', False)
        self.assertEqual(restarted.state['saved'], saved)
        restarted.reconcile('balanced', False)
        self.broker = restarted
        self.assert_restored()

    def test_partial_apply_failure_recovers_original_snapshot(self):
        original_write = m.write

        def fail_brightness(path, value):
            if Path(path).name == 'brightness' and value == 255:
                raise OSError('simulated interruption after colour write')
            original_write(path, value)

        with patch.object(m, 'write', side_effect=fail_brightness):
            with self.assertRaises(RuntimeError):
                self.broker.reconcile('performance', False)
        # Even incomplete lighting must be restored after a crash/profile exit.
        self.broker = m.Broker(self.cfg, self.root)
        self.broker.reconcile('balanced', False)
        self.assert_restored()

    def test_restore_failure_keeps_record_for_retry(self):
        self.broker.reconcile('performance', False)
        with patch.object(m, 'write', side_effect=OSError('temporarily unavailable')):
            with self.assertRaises(RuntimeError):
                self.broker.reconcile('balanced', False)
        self.assertIn('lighting', self.broker.state['saved'])
        self.broker = m.Broker(self.cfg, self.root)
        self.broker.reconcile('balanced', False)
        self.assert_restored()

    def test_game_subset_full_turbo_and_back_preserve_fans(self):
        self.broker.reconcile('performance', False, game=True)
        self.assert_restored()
        self.broker.reconcile('performance', False, game=True, max_fans=True)
        self.assertEqual(m.read(self.zone / 'multi_intensity'), '255 32 0')
        self.broker.reconcile('performance', False, game=True)
        self.assert_restored()
        self.assertEqual(m.read(self.pwm), '2')

    def test_game_can_opt_into_lighting_without_performance_profile(self):
        self.cfg['games']['addons']['lighting'] = True
        self.broker.reconcile('balanced', False, game=True)
        self.assertEqual(self.broker.state['applied'], ['lighting'])
        self.broker.reconcile('balanced', False, game=False)
        self.assert_restored()

    def test_subset_keeps_wanted_lighting_and_original_snapshot(self):
        self.cfg['games']['addons']['lighting'] = True
        self.broker.reconcile('performance', False, game=True, max_fans=True)
        saved = copy.deepcopy(self.broker.state['saved']['lighting'])
        self.broker = m.Broker(self.cfg, self.root)
        with patch.object(m, 'write', side_effect=AssertionError('wanted lighting was rewritten')):
            self.broker.reconcile('performance', False, game=True)
        self.assertEqual(self.broker.state['saved']['lighting'], saved)
        self.assertEqual(self.broker.state['applied'], ['lighting'])
        self.broker.reconcile('balanced', False)
        self.assert_restored()

    def test_skip_addons_unplug_replug(self):
        self.cfg['onBattery'] = 'skip-addons'
        self.broker.reconcile('performance', True, max_fans=True)
        self.assert_restored()
        self.broker.reconcile('performance', False, max_fans=True)
        self.broker.reconcile('performance', True, max_fans=True)
        self.assert_restored()
        self.broker.reconcile('performance', False, max_fans=True)

    def ppd(self, fail=None, keeps_performance=False):
        """Fake powerprofilesctl: a non-Turbo profile ends key Turbo, fans included."""
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if fail is not None:
                raise fail
            if not keeps_performance:
                self.profile.write_text(command[2])
                self.pwm.write_text('2')
            return subprocess.CompletedProcess(command, 0, '', '')
        patch.object(subprocess, 'run', side_effect=run).start()
        return calls

    def key_turbo(self):
        self.profile.write_text('performance')
        self.pwm.write_text('0')

    def poll(self, on_battery):
        """One daemon poll: sample the hardware like loop() and reconcile.

        Returns the broker's log output."""
        log = io.StringIO()
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            self.broker.reconcile(m.read(self.profile), on_battery, max_fans=m.read(self.pwm) == '0')
        return log.getvalue()

    def test_end_turbo_on_battery_ends_turbo_once_per_entry_and_releases(self):
        self.cfg['onBattery'] = 'end-turbo'
        calls = self.ppd()
        self.key_turbo()
        self.poll(False)
        self.assertEqual(self.broker.state['applied'], ['lighting'])
        self.assertEqual(calls, [])
        log = self.poll(True)  # Unplugged: ended through ppd, add-ons released.
        self.assertIn('ending Turbo entry 1', log)
        self.assertIn('Turbo ended', log)
        self.assertEqual(calls, [['powerprofilesctl', 'set', 'balanced']])
        self.assertEqual((m.read(self.profile), m.read(self.pwm)), ('balanced', '2'))
        self.assert_restored()
        self.assertEqual(self.broker.state['phase'], 'off')
        for on_battery in (True, True, False, False, True):  # Replug: nothing re-enters.
            self.poll(on_battery)
        self.assertEqual(calls, [['powerprofilesctl', 'set', 'balanced']])
        self.assertEqual((m.read(self.profile), m.read(self.pwm)), ('balanced', '2'))
        self.assert_restored()
        # A key press on battery is a new entry, ended on the next poll.
        self.key_turbo()
        self.poll(True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.broker.state['batteryEnded'], 2)
        self.assertEqual((m.read(self.profile), m.read(self.pwm)), ('balanced', '2'))
        self.assert_restored()

    def test_end_turbo_never_repeats_a_failed_or_restarted_entry(self):
        self.cfg['onBattery'] = 'end-turbo'
        calls = self.ppd(keeps_performance=True)  # Nothing ends Turbo.
        patch.object(m, 'write', side_effect=lambda path, value: None if Path(path) == self.profile
                     else Path(path).write_text(str(value))).start()
        self.key_turbo()
        self.poll(False)
        log = self.poll(True) + self.poll(True)
        self.broker = m.Broker(self.cfg, self.root)
        log += self.poll(True)
        self.assertEqual(len(calls), 1)
        self.assertIn('Turbo did not end', log)
        self.assert_restored()  # Add-ons stay released on battery regardless.

    def test_end_turbo_falls_back_to_sysfs_without_ppd(self):
        self.cfg['onBattery'] = 'end-turbo'
        for failure in (FileNotFoundError('powerprofilesctl'),
                        subprocess.CalledProcessError(1, 'powerprofilesctl'),
                        subprocess.TimeoutExpired('powerprofilesctl', 15)):
            with self.subTest(failure=type(failure).__name__):
                patch.stopall()
                self.setUp()
                self.cfg['onBattery'] = 'end-turbo'
                calls = self.ppd(fail=failure)
                # CLI Turbo: the broker wrote the fans, so it restores them.
                self.broker.set_turbo(True)
                self.poll(False)
                log = self.poll(True)
                self.assertEqual(len(calls), 1)
                self.assertIn('ppd unavailable', log)
                self.assertEqual((m.read(self.profile), m.read(self.pwm)), ('balanced', '2'))
                self.assertNotIn('hardware', self.broker.state)
                self.assert_restored()

    def test_end_turbo_on_ac_or_without_turbo_does_nothing(self):
        self.cfg['onBattery'] = 'end-turbo'
        calls = self.ppd()
        self.key_turbo()
        self.poll(False)
        self.assertEqual(self.broker.state['applied'], ['lighting'])
        self.profile.write_text('performance')
        self.pwm.write_text('2')  # Plain performance profile, not Turbo.
        self.poll(True)
        self.profile.write_text('balanced')
        self.poll(True)
        self.assertEqual(calls, [])
        self.assert_restored()

    def test_allow_and_skip_addons_never_end_turbo(self):
        calls = self.ppd()
        for policy in ('allow', 'skip-addons'):
            with self.subTest(policy=policy):
                self.cfg['onBattery'] = policy
                self.key_turbo()
                self.poll(False)
                self.poll(True)
                self.assertEqual((m.read(self.profile), m.read(self.pwm)), ('performance', '0'))
                self.assertEqual(self.broker.state['applied'], ['lighting'] if policy == 'allow' else [])
                self.poll(False)
                self.assertEqual(self.broker.state['applied'], ['lighting'])
                self.profile.write_text('balanced')
                self.pwm.write_text('2')
                self.poll(False)
                self.assert_restored()
        self.assertEqual(calls, [])

    def test_stay_on_releases_ownership_without_restoring_colour(self):
        self.cfg['addons']['lighting']['stayOn'] = True
        self.broker.reconcile('performance', False)
        self.broker.reconcile('balanced', False)
        self.assertEqual(m.read(self.zone / 'multi_intensity'), '255 32 0')
        self.assertEqual(self.broker.state['saved'], {})

    def timeout_device(self, seconds=30):
        path = self.root / 'kbd_backlight_timeout'
        path.write_text(str(seconds))
        patch.object(m, 'LIGHTING_TIMEOUT', path).start()
        return path

    def test_timeout_snapshot_survives_restart_and_restores_even_with_stay_on(self):
        timeout = self.timeout_device(30)
        self.cfg['addons']['lighting'].update(idleTimeoutSeconds=0, stayOn=True)
        self.broker.reconcile('performance', False)
        self.assertEqual(m.read(timeout), '0')
        self.assertEqual(self.broker.state['version'], 4)
        saved = copy.deepcopy(self.broker.state['saved'])
        restarted = m.Broker(self.cfg, self.root)
        with patch.object(restarted, 'apply', side_effect=AssertionError('reapplied')):
            restarted.reconcile('performance', False)
        self.assertEqual(restarted.state['saved'], saved)
        restarted.reconcile('balanced', False)
        self.assertEqual(m.read(timeout), '30')
        self.assertEqual(m.read(self.zone / 'multi_intensity'), '255 32 0')

    def test_timeout_default_does_not_require_new_driver(self):
        with patch.object(m, 'LIGHTING_TIMEOUT', self.root / 'missing'):
            self.broker.reconcile('performance', False)
            self.assertNotIn('lightingTimeout', self.broker.state['saved'])
            self.broker.reconcile('balanced', False)
        self.assert_restored()

    def test_timeout_partial_write_failure_retains_original_restore_data(self):
        timeout = self.timeout_device(45)
        self.cfg['addons']['lighting']['idleTimeoutSeconds'] = 0
        original_write = m.write

        def interrupted(path, value):
            original_write(path, value)
            if Path(path) == timeout and value == 0:
                raise OSError('write completed before disconnect')

        with patch.object(m, 'write', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'lightingTimeout apply'):
                self.broker.reconcile('performance', False)
        self.broker = m.Broker(self.cfg, self.root)
        self.broker.reconcile('balanced', False)
        self.assertEqual(m.read(timeout), '45')
        self.assert_restored()

    def test_timeout_readback_failure_and_restore_retry(self):
        timeout = self.timeout_device(30)
        self.cfg['addons']['lighting']['idleTimeoutSeconds'] = 15
        self.broker.reconcile('performance', False)
        original_write = m.write

        def ignored(path, value):
            if Path(path) != timeout:
                original_write(path, value)

        with patch.object(m, 'write', side_effect=ignored):
            with self.assertRaisesRegex(RuntimeError, 'timeout readback mismatch'):
                self.broker.reconcile('balanced', False)
        self.assertEqual(set(self.broker.state['saved']), {'lightingTimeout'})
        self.broker = m.Broker(self.cfg, self.root)
        self.broker.reconcile('balanced', False)
        self.assertEqual(m.read(timeout), '30')

    def test_timeout_tracks_game_lighting_and_battery_policy(self):
        timeout = self.timeout_device(20)
        self.cfg['addons']['lighting']['idleTimeoutSeconds'] = 255
        self.cfg['games']['addons']['lighting'] = True
        self.cfg['onBattery'] = 'skip-addons'
        self.broker.reconcile('balanced', False, game=True)
        self.assertEqual(m.read(timeout), '255')
        self.broker.reconcile('balanced', True, game=True)
        self.assertEqual(m.read(timeout), '20')

    def test_timeout_rejects_out_of_range_input_before_write(self):
        timeout = self.timeout_device(30)
        for value in (-1, 256, True, '0'):
            with self.assertRaises(ValueError):
                m.write_timeout(timeout, value)
        self.assertEqual(m.read(timeout), '30')

    def test_v1_state_migration_preserves_original_snapshot(self):
        self.broker.reconcile('performance', False)
        self.broker.state['version'] = 1
        self.broker.persist()
        self.broker = m.Broker(self.cfg, self.root)
        self.assertEqual(self.broker.state['version'], 4)
        self.broker.reconcile('balanced', False)
        self.assert_restored()

    def test_cli_repeated_on_keeps_snapshot_external_exit_invalidates(self):
        self.profile.write_text('quiet')
        self.broker.set_turbo(True)
        self.broker.set_turbo(True)
        self.assertEqual(self.broker.state['hardware']['profile'], 'quiet')
        self.broker.set_turbo(False)
        self.assertEqual(m.read(self.profile), 'quiet')
        self.assertEqual(m.read(self.pwm), '2')
        self.broker.set_turbo(True)
        self.profile.write_text('balanced')
        self.broker.reconcile('balanced', False)
        self.assertNotIn('hardware', self.broker.state)
        self.broker.set_turbo(False)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_stop_leaves_hardware_alone_and_restart_is_distinct(self):
        self.broker.set_turbo(True)
        self.broker.reconcile('performance', False)
        self.broker.signal(signal.SIGUSR1, None)
        self.assertEqual(self.broker.stopping, 'restart')
        self.broker.signal(signal.SIGTERM, None)
        self.assertEqual(self.broker.stopping, 'stop')
        self.broker.leave()
        self.assert_restored()
        self.assertEqual(m.read(self.profile), 'performance')
        self.assertEqual(m.read(self.pwm), '0')


    def test_stop_attempts_other_restores_after_one_failure(self):
        self.timeout_device()
        self.cfg['addons']['lighting']['idleTimeoutSeconds'] = 0
        self.broker.reconcile('performance', False)
        original = m.write
        def fail_colour(path, value):
            if Path(path).name == 'multi_intensity':
                raise OSError('lighting temporarily unavailable')
            original(path, value)
        with patch.object(m, 'write', side_effect=fail_colour):
            with self.assertRaises(RuntimeError):
                self.broker.leave()
        self.assertEqual(set(self.broker.state['saved']), {'lighting'})
        self.broker.leave()
        self.assert_restored()

    def test_retired_snapshots_are_rejected_without_discarding_data(self):
        for name in ('resources', 'gpuOffset', 'performanceMode'):
            saved = {'version': 4, 'phase': 'on', 'saved': {name: {'original': 1}}, 'applied': [name]}
            m.save(self.broker.path, saved)
            with self.assertRaisesRegex(ValueError, 'retired add-on'):
                m.Broker(self.cfg, self.root)
            self.assertEqual(m.load(self.broker.path, {}), saved)

    def start_process(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['games']['enable'] = False
        m.save(self.root / 'config.json', cfg)
        code = """
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('broker', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
r = Path(sys.argv[2])
m.PROFILE = r / 'platform_profile'
m.zones = lambda: [r / 'left']
m.fans = lambda: [r / 'pwm1_enable']
m.battery = lambda: False
m.Broker(json.loads((r / 'config.json').read_text()), r).run()
"""
        process = subprocess.Popen([sys.executable, '-c', code, str(Path(m.__file__)), str(self.root)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        return process

    def wait_process_state(self, process, phase):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.assertIsNone(process.poll(), process.communicate() if process.poll() is not None else '')
            state = m.load(self.root / 'state.json', {})
            if state.get('phase') == phase and (self.root / 'control').exists():
                return state
            time.sleep(0.05)
        self.fail(f'daemon never reached {phase}')

    def test_real_process_restart_preserves_and_sigterm_restores(self):
        self.profile.write_text('performance')
        self.pwm.write_text('0')
        process = self.start_process()
        initial = self.wait_process_state(process, 'on')
        timestamp = (self.zone / 'multi_intensity').stat().st_mtime_ns
        process.send_signal(signal.SIGUSR1)
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(m.read(self.zone / 'multi_intensity'), '255 32 0')
        process = self.start_process()
        state = self.wait_process_state(process, 'on')
        time.sleep(1.5)  # allow a real reconciliation after reading preserved state
        self.assertEqual(state['saved'], initial['saved'])
        self.assertEqual((self.zone / 'multi_intensity').stat().st_mtime_ns, timestamp)
        process.terminate()
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(m.read(self.zone / 'multi_intensity'), '61 174 233')
        self.assertEqual(m.read(self.zone / 'brightness'), '128')
        self.assertEqual(m.read(self.profile), 'performance')
        self.assertEqual(m.read(self.pwm), '0')

    def test_real_process_crash_recovers_and_excludes_second_daemon(self):
        self.profile.write_text('performance')
        process = self.start_process()
        self.wait_process_state(process, 'on')
        self.assertTrue(m.daemon_running(self.root))
        other = self.start_process()
        _, error = other.communicate(timeout=5)
        self.assertNotEqual(other.returncode, 0)
        self.assertIn(b'BlockingIOError', error)
        process.kill()
        process.communicate(timeout=5)
        self.assertTrue((self.root / 'control').exists())
        self.assertFalse(m.daemon_running(self.root))
        self.profile.write_text('balanced')
        process = self.start_process()
        self.wait_process_state(process, 'off')
        self.assertEqual(m.read(self.zone / 'multi_intensity'), '61 174 233')
        self.assertEqual(m.read(self.zone / 'brightness'), '128')
        process.terminate()
        process.communicate(timeout=5)

    def test_running_probe_does_not_create_paths_or_acquire_locks(self):
        missing = self.root / 'missing-runtime'
        self.assertFalse(m.daemon_running(missing))
        self.assertFalse(missing.exists())
        with socket.socket(socket.AF_UNIX) as endpoint:
            endpoint.bind(str(self.root / 'control'))
            self.assertFalse(m.daemon_running(self.root))
            lock_path = self.root / 'lock'
            lock_path.write_text('preserve existing contents')
            lock_path.chmod(0o444)
            with lock_path.open() as lock:
                m.fcntl.flock(lock, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
                with patch.object(m.fcntl, 'flock', side_effect=AssertionError('status acquired a lock')):
                    self.assertTrue(m.daemon_running(self.root))
            self.assertFalse(m.daemon_running(self.root))
            self.assertEqual(lock_path.read_text(), 'preserve existing contents')
            self.assertEqual(lock_path.stat().st_mode & 0o777, 0o444)


    def test_disabled_addons_never_touched(self):
        for addon in self.cfg['addons'].values():
            addon['enable'] = False
        with patch.object(self.broker, 'snapshot', side_effect=AssertionError('touched')):
            self.broker.reconcile('performance', False)
        self.assertEqual(self.broker.state['phase'], 'off')



class BatteryDetectionTests(unittest.TestCase):
    """battery() takes line power from line-power supplies only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(m, 'POWER_SUPPLY', self.root).start()
        # The PT516-52s as listed on the author's machine, unplugged.
        self.supply('ACAD', type='Mains', online='0')
        self.supply('BAT1', type='Battery', status='Discharging')
        self.supply('ucsi-source-psy-USBC000:001', type='USB', online='0', scope='Device')

    def supply(self, name, **attrs):
        d = self.root / name
        d.mkdir()
        for key, value in attrs.items():
            (d / key).write_text(value + '\n')

    def test_laptop_alone_unplugged_is_on_battery(self):
        self.assertTrue(m.battery())

    def test_peripheral_battery_does_not_mask_discharge(self):
        # drivers/hid/hid-input.c reports POWER_SUPPLY_PROP_ONLINE = 1 for
        # every HID device battery (mouse, keyboard, gamepad), scope Device.
        self.supply('hid-00:11:22:33:44:55-battery', type='Battery', online='1',
                    scope='Device', status='Discharging')
        self.assertTrue(m.battery(), 'unplugged laptop reported as on AC')


if __name__ == '__main__':
    unittest.main()
