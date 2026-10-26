"""Game profile ownership uses fake sysfs; no host hardware writes."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_turbo_broker import CONFIG, m


class GameProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profile'
        self.profile.write_text('balanced')
        self.fan = self.root / 'pwm1_enable'
        self.fan.write_text('2')
        self.cfg = copy.deepcopy(CONFIG)
        self.cfg['games']['performanceProfile'] = True
        self.addCleanup(patch.stopall)
        patch.object(m, 'PROFILE', self.profile).start()
        patch.object(m, 'fans', return_value=[self.fan]).start()
        self.broker = m.Broker(self.cfg, self.root)

    def key(self, on):
        # Fixture for the kernel's observed profile/fan transaction, not CLI.
        self.profile.write_text('performance' if on else 'balanced')
        self.fan.write_text('0' if on else '2')
        self.broker.observe_game_profile()

    def test_game_alone_restores_exact_original_profile(self):
        self.profile.write_text('quiet')
        self.broker.game_profile(True)
        self.assertEqual(m.read(self.profile), 'performance')
        self.assertEqual(m.read(self.fan), '2')
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'quiet')
        self.assertNotIn('gameProfile', self.broker.state)

    def test_turbo_first_game_exit_does_not_change_hardware(self):
        self.key(True)
        self.broker.game_profile(True)
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'performance')
        self.assertEqual(m.read(self.fan), '0')

    def test_game_first_game_exit_does_not_cancel_key_turbo(self):
        self.broker.game_profile(True)
        self.key(True)
        self.assertNotIn('restore', self.broker.state['gameProfile'])
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'performance')
        self.assertEqual(m.read(self.fan), '0')

    def test_key_off_during_game_does_not_reacquire_after_restart_or_duplicate(self):
        self.broker.game_profile(True)
        self.key(True)
        self.key(False)
        self.broker = m.Broker(self.cfg, self.root)
        self.broker.game_profile(True)
        self.broker.observe_game_profile()
        self.assertEqual(m.read(self.profile), 'balanced')
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_key_entry_before_next_poll_is_checked_at_game_exit(self):
        self.broker.game_profile(True)
        self.profile.write_text('performance')
        self.fan.write_text('0')
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'performance')
        self.assertEqual(m.read(self.fan), '0')

    def test_external_profile_wins(self):
        self.broker.game_profile(True)
        self.profile.write_text('quiet')
        self.broker.observe_game_profile()
        self.broker.game_profile(True)
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'quiet')

    def test_stop_start_preserves_yielded_game_and_entry_identity(self):
        self.broker.game_profile(True)
        self.key(True)
        self.key(False)
        self.broker.state['observed'] = {'available': True, 'turbo': False, 'entry': 9}
        self.broker.stop_effects()
        self.assertEqual(self.broker.state['observed']['entry'], 9)
        self.assertFalse(self.broker.state['observed']['available'])
        self.broker = m.Broker(self.cfg, self.root)
        self.broker.game_profile(True)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_stop_restores_owned_profile_without_reapplying_failed_entry(self):
        self.broker.game_profile(True)
        self.broker.stop_effects()
        self.assertEqual(m.read(self.profile), 'balanced')
        self.assertNotIn('gameProfile', self.broker.state)
        with patch.object(m, 'write', side_effect=OSError('failed write')):
            with self.assertRaises(OSError):
                self.broker.game_profile(True)
        with patch.object(m, 'write', side_effect=AssertionError('stop reapplied profile')):
            self.broker.stop_effects()

    def test_preexisting_performance_is_not_owned(self):
        self.profile.write_text('performance')
        self.broker.game_profile(True)
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'performance')

    def test_duplicate_on_and_lost_reply_keep_original_snapshot(self):
        self.broker.game_profile(True)
        self.broker = m.Broker(self.cfg, self.root)
        self.broker.game_profile(True)
        self.assertEqual(self.broker.state['gameProfile']['restore'], 'balanced')
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_failed_apply_is_journaled_and_retryable(self):
        original_write = m.write
        with patch.object(m, 'write', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.broker.game_profile(True)
        self.assertEqual(self.broker.state['gameProfile']['phase'], 'entering')
        self.broker = m.Broker(self.cfg, self.root)
        with patch.object(m, 'write', wraps=original_write):
            self.broker.game_profile(True)
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_failed_restore_survives_restart_and_retries(self):
        self.broker.game_profile(True)
        with patch.object(m, 'write', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.broker.game_profile(False)
        self.broker = m.Broker(self.cfg, self.root)
        self.assertFalse(self.broker.state['gameProfile']['requested'])
        self.broker.observe_game_profile()
        self.assertEqual(m.read(self.profile), 'balanced')
        self.assertNotIn('gameProfile', self.broker.state)

    def test_unknown_or_missing_fans_cannot_authorize_restore(self):
        self.broker.game_profile(True)
        self.fan.write_text('unknown')
        with self.assertRaises(RuntimeError):
            self.broker.game_profile(False)
        self.assertIn('restore', self.broker.state['gameProfile'])
        self.fan.write_text('0')
        self.broker.game_profile(False)
        self.assertEqual(m.read(self.profile), 'performance')
        with patch.object(m, 'fans', return_value=[]):
            with self.assertRaises(RuntimeError):
                self.broker.game_profile(True)

    def test_config_disable_releases_old_request_but_rejects_new_one(self):
        self.broker.game_profile(True)
        self.cfg['games']['enable'] = False
        self.broker.game_profile(False)
        with self.assertRaises(ValueError):
            self.broker.game_profile(True)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_game_lighting_lifetime_without_profile_request(self):
        self.cfg['games']['performanceProfile'] = False
        self.broker.game_profile(True)
        self.assertTrue(self.broker.game_active())
        self.assertEqual(m.read(self.profile), 'balanced')
        self.broker.game_profile(False)
        self.assertFalse(self.broker.game_active())

    def test_profile_disabled_on_restart_releases_snapshot_but_keeps_game(self):
        self.broker.game_profile(True)
        self.cfg['games']['performanceProfile'] = False
        self.broker = m.Broker(self.cfg, self.root)
        self.broker.reconcile('performance', False, game=True)
        self.assertEqual(m.read(self.profile), 'balanced')
        self.assertTrue(self.broker.game_active())
        self.assertNotIn('restore', self.broker.state['gameProfile'])

    def test_group_can_request_game_but_cannot_toggle_turbo(self):
        with patch.object(m.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=983)):
            self.broker.command('game-profile on', 1000, [100, 983])
            self.assertEqual(m.read(self.profile), 'performance')
            for command in ('on', 'off', 'unknown'):
                with self.assertRaises(PermissionError):
                    self.broker.command(command, 1000, [100, 983])
            with self.assertRaises(PermissionError):
                self.broker.command('game-profile off', 65534, [65534])
            self.broker.command('game-profile off', 1000, [100, 983])
            self.assertEqual(m.read(self.profile), 'balanced')


class BootPolicyTests(unittest.TestCase):
    def setUp(self):
        GameProfileTests.setUp(self)
        # Never let an offline test call the host's ppd.
        self.ppd = self.enterContext(patch('subprocess.run'))

    def test_ppd_restore_is_serialized_before_fans_and_readback(self):
        boot = self.root / 'boot-id'
        boot.write_text('first-boot')
        self.profile.write_text('performance')
        self.fan.write_text('0')
        def ppd_restore_then_set(*args, **kwargs):
            self.assertEqual(m.read(self.profile), 'balanced')
            self.assertEqual(m.read(self.fan), '0')
            # The daemon restores its saved performance before handling Set.
            self.profile.write_text('performance')
            self.assertEqual(args[0], ['powerprofilesctl', 'set', 'balanced'])
            self.profile.write_text('balanced')
        self.ppd.side_effect = ppd_restore_then_set
        m.reset_boot(self.root, boot)
        self.assertEqual(m.read(self.profile), 'balanced')
        self.assertEqual(m.read(self.fan), '2')

    def test_missing_ppd_falls_back_to_sysfs(self):
        boot = self.root / 'boot-id'
        boot.write_text('first-boot')
        self.ppd.side_effect = FileNotFoundError()
        m.reset_boot(self.root, boot)
        self.assertEqual(m.read(self.profile), 'balanced')
        self.assertEqual(m.read(self.fan), '2')

    def test_new_boot_resets_but_same_boot_restart_preserves_turbo(self):
        boot = self.root / 'boot-id'
        boot.write_text('first-boot')
        self.profile.write_text('performance')
        self.fan.write_text('0')
        m.reset_boot(self.root, boot)
        self.assertEqual(m.read(self.profile), 'balanced')
        self.assertEqual(m.read(self.fan), '2')
        self.profile.write_text('performance')
        self.fan.write_text('0')
        with patch.object(m, 'write', side_effect=AssertionError('reset again')):
            m.reset_boot(self.root, boot)
        self.assertEqual(m.read(self.profile), 'performance')
        boot.write_text('second-boot')
        m.reset_boot(self.root, boot)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_partial_boot_reset_does_not_mark_success(self):
        boot = self.root / 'boot-id'
        boot.write_text('first-boot')
        self.profile.write_text('performance')
        self.fan.write_text('0')
        original_write = m.write

        def fail_fan(path, value):
            if path == self.fan:
                raise OSError('fan write failed')
            original_write(path, value)

        with patch.object(m, 'write', side_effect=fail_fan):
            with self.assertRaises(OSError):
                m.reset_boot(self.root, boot)
        self.assertFalse((self.root / 'boot-policy.json').exists())
        m.reset_boot(self.root, boot)
        self.assertEqual(m.read(self.fan), '2')

    def test_running_hardware_owner_excludes_boot_reset(self):
        boot = self.root / 'boot-id'
        boot.write_text('first-boot')
        with (self.root / 'lock').open('w') as lock:
            m.fcntl.flock(lock, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                m.reset_boot(self.root, boot)
        self.assertFalse((self.root / 'boot-policy.json').exists())


class GameProfileLeaseTests(unittest.TestCase):
    """The request is leased to its sender's process; the journal records the owner."""

    def setUp(self):
        GameProfileTests.setUp(self)
        self.cfg['profileAddons'] = []  # plain performance wants no lighting here
        self.zone = self.root / 'left'
        self.zone.mkdir()
        for name, value in {'multi_intensity': '61 174 233', 'brightness': '255', 'max_brightness': '255'}.items():
            (self.zone / name).write_text(value)
        patch.object(m, 'zones', return_value=[self.zone]).start()
        self.alive = {}  # pid -> start time of the processes the fake /proc knows
        self.clock = [1000.0]
        patch.object(m, 'process_identity', side_effect=lambda pid: (
            {'pid': pid, 'start': self.alive[pid]} if pid in self.alive else None)).start()
        patch.object(m, 'now', side_effect=lambda: self.clock[0]).start()

    def key(self, on):
        GameProfileTests.key(self, on)

    def request(self, pid, start=7):
        self.alive[pid] = start
        self.broker.command('game-profile on', 0, [], pid=pid)

    def tick(self, seconds):
        self.clock[0] += seconds
        self.broker.reconcile(m.read(self.profile), False, self.broker.game_active(),
                              m.read(self.fan) == '0')

    def test_owner_death_releases_after_grace_and_restores_owned_profile(self):
        self.request(4242)
        self.assertEqual(m.read(self.profile), 'performance')
        self.assertEqual(self.broker.state['gameProfile']['owner'], {'pid': 4242, 'start': 7})
        del self.alive[4242]
        self.tick(1)
        self.assertTrue(self.broker.state['gameProfile']['requested'])
        self.assertIn('ownerLostSince', self.broker.state['gameProfile'])
        self.assertEqual(m.read(self.profile), 'performance')
        self.tick(m.LEASE_GRACE_SECONDS - 2)
        self.assertIn('gameProfile', self.broker.state)
        self.tick(2)  # loss is timed from its first observation
        self.assertNotIn('gameProfile', self.broker.state)
        self.assertEqual(m.read(self.profile), 'balanced')
        self.assertEqual(self.broker.state['mode'], 'off')

    def test_healthy_agent_restart_transfers_lease_and_keeps_yielded_phase(self):
        self.request(4242)
        self.key(True)
        self.assertEqual(self.broker.state['gameProfile']['phase'], 'yielded')
        del self.alive[4242]
        self.tick(3)
        self.assertIn('ownerLostSince', self.broker.state['gameProfile'])
        self.request(4300)  # the restarted agent re-sends within the grace period
        request = self.broker.state['gameProfile']
        self.assertEqual(request['owner'], {'pid': 4300, 'start': 7})
        self.assertNotIn('ownerLostSince', request)
        self.assertEqual(request['phase'], 'yielded')
        self.tick(m.LEASE_GRACE_SECONDS + 5)
        self.assertIn('gameProfile', self.broker.state)
        self.key(False)
        self.tick(1)
        # Ownership passed to the key; the surviving request never reacquires.
        self.assertEqual(m.read(self.profile), 'balanced')
        self.assertEqual(self.broker.state['gameProfile']['phase'], 'yielded')

    def test_stale_owner_cannot_clear_a_new_owner(self):
        self.request(4242)
        del self.alive[4242]
        self.tick(1)
        self.request(4300)
        self.tick(m.LEASE_GRACE_SECONDS + 5)  # the old owner is long gone
        self.assertIn('gameProfile', self.broker.state)
        self.assertEqual(m.read(self.profile), 'performance')
        del self.alive[4300]
        self.tick(1)
        self.tick(m.LEASE_GRACE_SECONDS)
        self.assertNotIn('gameProfile', self.broker.state)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_pid_reuse_is_not_the_owner(self):
        self.request(4242, start=7)
        self.alive[4242] = 99  # a different process now has that pid
        self.tick(1)
        self.tick(m.LEASE_GRACE_SECONDS)
        self.assertNotIn('gameProfile', self.broker.state)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_lease_survives_broker_restart_while_owner_lives(self):
        self.request(4242)
        self.broker = m.Broker(self.cfg, self.root)
        self.tick(m.LEASE_GRACE_SECONDS + 5)
        self.assertEqual(self.broker.state['gameProfile']['owner'], {'pid': 4242, 'start': 7})
        self.assertEqual(m.read(self.profile), 'performance')

    def test_short_lived_cli_request_lapses_after_grace(self):
        self.request(5000)
        del self.alive[5000]  # a CLI sender exits as soon as it has its reply
        self.tick(1)
        self.tick(m.LEASE_GRACE_SECONDS)
        self.assertNotIn('gameProfile', self.broker.state)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_request_from_a_process_already_gone_takes_no_lease(self):
        with self.assertRaises(RuntimeError):
            self.broker.command('game-profile on', 0, [], pid=6000)
        self.assertNotIn('gameProfile', self.broker.state)
        self.assertEqual(m.read(self.profile), 'balanced')

    def test_unleased_request_keeps_legacy_lifetime(self):
        self.broker.game_profile(True)  # no owner recorded (tests, in-process use)
        self.tick(m.LEASE_GRACE_SECONDS + 5)
        self.assertIn('gameProfile', self.broker.state)
        self.assertEqual(m.read(self.profile), 'performance')

    def test_lost_owner_release_does_not_apply_game_lighting_for_the_sampled_cycle(self):
        self.cfg['games']['addons']['lighting'] = True
        self.request(4242)
        del self.alive[4242]
        self.tick(1)
        self.clock[0] += m.LEASE_GRACE_SECONDS
        # Stale sample: the caller still believes the game is active.
        self.broker.reconcile('performance', False, True, False)
        self.assertNotIn('gameProfile', self.broker.state)
        self.assertEqual(self.broker.state['mode'], 'off')
        self.assertEqual(self.broker.state['saved'], {})

    def test_live_owner_keeps_its_lease_against_a_stray_request(self):
        self.request(4242)
        self.request(5000)  # a shell's game-profile on while the agent owns
        self.assertEqual(self.broker.state['gameProfile']['owner'], {'pid': 4242, 'start': 7})
        del self.alive[5000]
        self.tick(1)
        self.tick(m.LEASE_GRACE_SECONDS + 1)
        self.assertEqual(self.broker.state['gameProfile']['owner'], {'pid': 4242, 'start': 7})
        self.assertEqual(m.read(self.profile), 'performance')
        self.assertNotIn('ownerLostSince', self.broker.state['gameProfile'])

    def test_profile_disable_on_restart_keeps_the_owner(self):
        self.request(4242)
        self.cfg['games']['performanceProfile'] = False
        self.broker = m.Broker(self.cfg, self.root)
        self.tick(1)
        request = self.broker.state['gameProfile']
        self.assertEqual(m.read(self.profile), 'balanced')
        self.assertEqual(request['owner'], {'pid': 4242, 'start': 7})
        del self.alive[4242]
        self.tick(1)
        self.tick(m.LEASE_GRACE_SECONDS)
        self.assertNotIn('gameProfile', self.broker.state)

    def test_grace_does_not_rewrite_the_journal_every_cycle(self):
        self.request(4242)
        del self.alive[4242]
        self.tick(1)
        stamp = self.broker.path.stat().st_mtime_ns
        self.tick(1)
        self.tick(1)
        self.assertEqual(self.broker.path.stat().st_mtime_ns, stamp)


class ProcessIdentityTests(unittest.TestCase):
    def test_real_process_identity_reads_proc(self):
        me = m.process_identity(os.getpid())
        self.assertEqual(me['pid'], os.getpid())
        self.assertGreater(me['start'], 0)
        self.assertTrue(m.lease_alive(me))
        self.assertFalse(m.lease_alive(dict(me, start=me['start'] + 1)))
        self.assertIsNone(m.process_identity(2 ** 22 + 1))
        self.assertIsNone(m.process_identity(0))
        self.assertIsNone(m.process_identity(None))

    def test_stat_parsing_handles_odd_names_zombies_and_garbage(self):
        tail = (
            b' S 1 2 3 4 -1 4194560 100 0 0 0 1 2 0 0 20 0 1 0 424242 1000 1 18446744073709551615'
            b' 1 1 0 0 0 0 0 0 0 0 0 0 17 3 0 0 0 0 0 0 0 0 0 0 0 0 0\n'
        )
        self.assertEqual(m.parse_stat(7, b'7 (x\xffy) b (c)' + tail), {'pid': 7, 'start': 424242})
        self.assertEqual(m.parse_stat(7, b'7 (a) b (c) d)' + tail)['start'], 424242)
        self.assertIsNone(m.parse_stat(7, b'7 (gone) Z 1 2 3 4 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 424242 0 0\n'))
        self.assertIsNone(m.parse_stat(7, b'7 (short) S 1\n'))
        self.assertIsNone(m.parse_stat(7, b'garbage'))
