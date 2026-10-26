"""Session ownership tests; no user services or hardware are changed."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'agent', Path(__file__).resolve().parents[1] / 'pkgs/turbo-agent/agent.py'
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
CONFIG = {'hardwareSocket': '/unused', 'games': True, 'gameUnit': 'turbo-game.service',
          'performanceMode': True, 'profile': False, 'onBattery': 'allow', 'startTimeoutSeconds': 5}
TURBO = {'available': True, 'turbo': True, 'profile': 'performance', 'onBattery': False, 'entry': 1}
OFF = {'available': True, 'turbo': False, 'profile': 'balanced', 'onBattery': False, 'entry': 1}


class Backend:
    def __init__(self):
        self.calls = []
        self.fail_game = False
        self.fail_start = False

    async def start(self, unit):
        self.calls.append(('start', unit))
        if self.fail_start:
            raise OSError('lost start reply')

    async def stop(self, unit):
        self.calls.append(('stop', unit))

    async def game_profile(self, on):
        self.calls.append(('game', on))
        if self.fail_game:
            raise OSError('hardware unavailable')


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = Backend()
        self.cfg = copy.deepcopy(CONFIG)
        self.agent = m.Agent(self.cfg, self.root, self.backend)

    async def test_start_is_pending_until_active_and_restart_does_not_reapply(self):
        await self.agent.reconcile(TURBO, {}, now=0)
        self.assertEqual(self.agent.state['phase'], 'entering')
        self.assertTrue(self.agent.state['startRequested'])
        await self.agent.reconcile(TURBO, {m.HOLD: 'activating'}, now=1)
        self.assertEqual(self.backend.calls.count(('start', m.HOLD)), 1)
        await self.agent.reconcile(TURBO, {m.HOLD: 'active'}, now=2)
        self.assertEqual(self.agent.state['phase'], 'on')
        self.agent = m.Agent(self.cfg, self.root, self.backend)
        await self.agent.reconcile(TURBO, {m.HOLD: 'active'}, now=3)
        self.assertEqual(self.backend.calls.count(('start', m.HOLD)), 1)

    async def test_opt_out_survives_restart_and_battery_until_next_entry(self):
        await self.agent.reconcile(TURBO, {})
        await self.agent.release()
        self.agent = m.Agent(self.cfg, self.root, self.backend)
        await self.agent.reconcile(TURBO, {})
        self.assertFalse(self.agent.state['wanted'])
        await self.agent.reconcile(dict(TURBO, onBattery=True), {})
        await self.agent.reconcile(TURBO, {})
        self.assertTrue(self.agent.state['optOut'])
        await self.agent.reconcile(dict(TURBO, entry=2), {})
        self.assertTrue(self.agent.state['wanted'])
        self.assertFalse(self.agent.state['optOut'])

    async def test_plain_profile_does_not_hold_desktop_and_profile_opt_in_does(self):
        await self.agent.reconcile(dict(OFF, profile='performance'), {})
        self.assertNotIn(('start', m.HOLD), self.backend.calls)
        self.cfg['profile'] = True
        await self.agent.reconcile(dict(OFF, profile='performance'), {})
        self.assertIn(('start', m.HOLD), self.backend.calls)

    async def test_unavailable_hardware_does_not_reset_opt_out_or_entry(self):
        await self.agent.reconcile(TURBO, {})
        await self.agent.release()
        await self.agent.reconcile({'available': False}, {})
        self.agent = m.Agent(self.cfg, self.root, self.backend)
        await self.agent.reconcile(TURBO, {})
        self.assertTrue(self.agent.state['optOut'])
        self.assertFalse(self.agent.state['wanted'])
        await self.agent.reconcile(dict(TURBO, entry=2), {})
        self.assertTrue(self.agent.state['wanted'])

    async def test_game_keeps_app_hold_when_turbo_releases(self):
        units = {'turbo-game.service': 'active', 'performance-mode-app.service': 'active', m.HOLD: 'active'}
        await self.agent.reconcile(TURBO, units)
        await self.agent.reconcile(OFF, units)
        self.assertIn(('stop', m.HOLD), self.backend.calls)
        self.assertNotIn(('stop', 'performance-mode-app.service'), self.backend.calls)
        self.assertIn('performance-mode-app.service', self.agent.state['holders'])
        self.assertTrue(self.agent.state['game'])

    async def test_failed_hardware_request_does_not_prevent_desktop_release(self):
        await self.agent.reconcile(TURBO, {})
        self.backend.fail_game = True
        await self.agent.reconcile(OFF, {m.HOLD: 'active'})
        self.assertIn(('stop', m.HOLD), self.backend.calls)
        self.assertEqual(self.agent.state['error'], 'hardware unavailable')

    async def test_battery_releases_owned_hold_but_not_game_request(self):
        self.cfg['onBattery'] = 'skip-addons'
        await self.agent.reconcile(TURBO, {})
        await self.agent.reconcile(dict(TURBO, onBattery=True), {'turbo-game.service': 'active', m.HOLD: 'active'})
        self.assertIn(('game', True), self.backend.calls)
        self.assertFalse(self.agent.state['wanted'])
        await self.agent.reconcile(TURBO, {}, now=100000)
        self.assertTrue(self.agent.state['wanted'])

    async def test_end_turbo_never_starts_the_hold_on_battery(self):
        # The broker ends Turbo on its next poll; the hold must not start meanwhile.
        self.cfg['onBattery'] = 'end-turbo'
        await self.agent.reconcile(dict(TURBO, onBattery=True, entry=2), {})
        self.assertNotIn(('start', m.HOLD), self.backend.calls)
        self.assertFalse(self.agent.state['wanted'])
        await self.agent.reconcile(dict(TURBO, entry=3), {}, now=100000)
        self.assertTrue(self.agent.state['wanted'])

    async def test_stop_and_unavailable_hardware_release_only_own_requests(self):
        await self.agent.reconcile(TURBO, {})
        await self.agent.reconcile({'available': False}, {m.HOLD: 'active'})
        self.assertFalse(self.agent.state['wanted'])
        await self.agent.stop()
        self.assertEqual(self.backend.calls[-2:], [('stop', m.HOLD), ('game', False)])

    async def test_failed_start_is_owned_across_restart_and_released(self):
        self.backend.fail_start = True
        with self.assertRaises(OSError):
            await self.agent.reconcile(TURBO, {})
        self.agent = m.Agent(self.cfg, self.root, self.backend)
        self.assertTrue(self.agent.state['startRequested'])
        await self.agent.reconcile(OFF, {})
        self.assertIn(('stop', m.HOLD), self.backend.calls)
        self.assertFalse(self.agent.state['startRequested'])

    async def test_start_deadline_is_visible_and_success_clears_error(self):
        await self.agent.reconcile(TURBO, {}, now=0)
        await self.agent.reconcile(TURBO, {m.HOLD: 'activating'}, now=10)
        self.assertIn('deadline', self.agent.state['error'])
        await self.agent.reconcile(TURBO, {m.HOLD: 'active'}, now=11)
        self.assertNotIn('error', self.agent.state)

    async def test_disabled_effect_releases_and_private_journal_persists(self):
        await self.agent.reconcile(TURBO, {})
        self.cfg['performanceMode'] = False
        await self.agent.reconcile(TURBO, {m.HOLD: 'active'})
        self.assertFalse(self.agent.state['wanted'])
        self.assertEqual(self.agent.path.stat().st_mode & 0o777, 0o600)
