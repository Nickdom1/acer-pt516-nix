"""Session policy requests. Hardware state and permissions come from Nix."""
import argparse
import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import time

HOLD = 'performance-mode-turbo.service'
BUS_NAME = 'org.detello.TurboAgent1'
BUS_PATH = '/org/detello/TurboAgent1'


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


class Agent:
    def __init__(self, config, runtime, backend):
        self.cfg, self.runtime, self.backend = config, runtime, backend
        self.path = runtime / 'state.json'
        self.state = load(self.path, {'version': 1, 'phase': 'off', 'optOut': False,
                                      'lastTurbo': False, 'startRequested': False})
        if self.state.get('version') != 1:
            raise ValueError('unknown agent state version')

    def persist(self):
        save(self.path, self.state)

    async def reconcile(self, observed, units, now=None):
        now = time.monotonic() if now is None else now
        previous = json.dumps(self.state, sort_keys=True)
        turbo = observed.get('available', False) and observed.get('turbo', False)
        if observed.get('available', False):
            if turbo and (not self.state['lastTurbo'] or observed.get('entry') != self.state.get('entry')):
                self.state['optOut'] = False
            self.state['lastTurbo'] = turbo
            self.state['entry'] = observed.get('entry')
        game = self.cfg['games'] and units.get(self.cfg['gameUnit']) == 'active'
        # Duplicate requests are intentional: daemon restart and lost replies
        # must recover without reacquiring a profile yielded to the key.
        request_error = None
        if self.cfg.get('hardwareSocket'):
            try:
                await self.backend.game_profile(game)
            except Exception as exc:
                request_error = str(exc)
        wanted = self.cfg['performanceMode'] and observed.get('available', False) and (
            turbo or (self.cfg['profile'] and observed.get('profile') == 'performance' and not game))
        # end-turbo: the hardware owner ends Turbo on battery; do not start
        # the hold in the moment before it does.
        if self.cfg['onBattery'] in ('skip-addons', 'end-turbo') and observed.get('onBattery'):
            wanted = False
        wanted = wanted and not self.state['optOut']
        active = units.get(HOLD, 'inactive')
        self.state.update(wanted=wanted, game=game, holders=[
            name for name in ('performance-mode-app.service', 'performance-mode-manual.service', HOLD)
            if units.get(name) == 'active'])
        if wanted:
            if active == 'active':
                self.state['phase'] = 'on'
                self.state.pop('pendingSince', None)
                self.state.pop('error', None)
            elif active == 'activating':
                self.state['phase'] = 'entering'
                since = self.state.setdefault('pendingSince', now)
                if now - since > self.cfg.get('startTimeoutSeconds', 330):
                    self.state['error'] = 'performance-mode hold did not become active before its deadline'
            elif now >= self.state.get('retryAfter', 0):
                self.state.update(startRequested=True, phase='entering', retryAfter=now + 2)
                self.state.setdefault('pendingSince', now)
                self.persist()  # Own cleanup even if the request reply is lost.
                await self.backend.start(HOLD)  # D-Bus returns a job path, not job completion.
            if active == 'failed':
                self.state['error'] = 'performance-mode hold failed; retrying'
            if self.state.get('pendingSince') is not None and now - self.state['pendingSince'] > self.cfg.get(
                'startTimeoutSeconds', 330
            ):
                self.state['error'] = 'performance-mode hold did not become active before its deadline'
        else:
            if self.state['startRequested'] or active in ('active', 'activating'):
                await self.backend.stop(HOLD)
                self.state['phase'] = 'leaving'
                if active in ('inactive', 'failed'):
                    self.state.update(startRequested=False, phase='off')
            else:
                self.state['phase'] = 'off'
            if self.state['phase'] == 'off':
                self.state.pop('pendingSince', None)
                self.state.pop('error', None)
        if request_error:
            self.state['error'] = request_error
        if json.dumps(self.state, sort_keys=True) != previous:
            self.persist()

    async def release(self):
        self.state['optOut'] = True
        self.persist()
        await self.backend.stop(HOLD)
        self.state['phase'] = 'leaving'
        self.persist()

    async def stop(self):
        # Independent app/manual holds remain owned by their request units.
        try:
            await self.backend.stop(HOLD)
        finally:
            await self.backend.game_profile(False)


class Backend:
    def __init__(self, bus, config):
        self.bus, self.cfg = bus, config
        self.last_game = None
        self.last_socket_identity = None

    async def call(self, member, signature='', body=None):
        from dbus_next import Message, MessageType
        reply = await asyncio.wait_for(self.bus.call(Message(
            destination='org.freedesktop.systemd1', path='/org/freedesktop/systemd1',
            interface='org.freedesktop.systemd1.Manager', member=member,
            signature=signature, body=body or [])), 10)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f'systemd {member}: {reply.error_name}')
        return reply.body

    async def units(self):
        return {row[0]: row[3] for row in (await self.call('ListUnits'))[0]}

    async def start(self, unit):
        await self.call('StartUnit', 'ss', [unit, 'replace'])

    async def stop(self, unit):
        await self.call('StopUnit', 'ss', [unit, 'replace'])

    async def game_profile(self, on):
        path = self.cfg.get('hardwareSocket')
        if not path:
            return
        try:
            info = Path(path).stat()
            identity = (info.st_dev, info.st_ino, info.st_ctime_ns)
        except FileNotFoundError:
            self.last_game = None
            if on:
                # Preserve implicit exception context; explicit chaining changes error metadata.
                raise RuntimeError('hardware owner unavailable for game request')  # noqa: B904
            return
        if on == self.last_game and identity == self.last_socket_identity:
            return
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(path), 3)
        try:
            writer.write(b'game-profile on' if on else b'game-profile off')
            await writer.drain()
            reply = json.loads(await asyncio.wait_for(reader.read(4096), 5))
            if not reply['ok']:
                raise RuntimeError(reply.get('error', 'hardware request failed'))
            self.last_game, self.last_socket_identity = on, identity
        finally:
            writer.close()
            await writer.wait_closed()


async def serve(config, runtime):
    from dbus_next import Message, MessageType, NameFlag, RequestNameReply
    from dbus_next.aio import MessageBus
    from dbus_next.service import ServiceInterface, method
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (runtime / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        bus = await MessageBus().connect()
        backend = Backend(bus, config)
        agent = Agent(config, runtime, backend)
        stop, wake = asyncio.Event(), asyncio.Event()
        guard = asyncio.Lock()
        loop = asyncio.get_running_loop()
        preserve = False

        def restart():
            nonlocal preserve
            preserve = True
            stop.set()
        loop.add_signal_handler(signal.SIGUSR1, restart)
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
        # Unit transitions wake the observer; no systemctl processes are polled.
        bus.add_message_handler(lambda message: wake.set() if message.message_type.name == 'SIGNAL' else None)
        await backend.call('Subscribe')
        match = await bus.call(Message(destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                                       interface='org.freedesktop.DBus', member='AddMatch', signature='s',
                                       body=["type='signal',sender='org.freedesktop.systemd1'"]))
        if match.message_type == MessageType.ERROR:
            raise RuntimeError('cannot subscribe to systemd unit changes')

        class Interface(ServiceInterface):
            def __init__(self):
                super().__init__(BUS_NAME)

            @method()
            def Status(self) -> 's':  # noqa: F821 -- D-Bus signature, not a Python type.
                return json.dumps(agent.state)

            @method()
            async def ReleasePerformanceMode(self) -> 's':  # noqa: F821
                async with guard:
                    await agent.release()
                wake.set()
                return json.dumps(agent.state)

        bus.export(BUS_PATH, Interface())
        if await bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE) != RequestNameReply.PRIMARY_OWNER:
            raise RuntimeError('another session agent owns the bus name')
        try:
            while not stop.is_set():
                wake.clear()
                async with guard:
                    try:
                        observed = load(config['hardwareStateFile'], {}).get('observed', {})
                        await agent.reconcile(observed, await backend.units())
                    except Exception as exc:
                        message = str(exc)
                        if agent.state.get('error') != message:
                            agent.state['error'] = message
                            agent.persist()
                            print(message, flush=True)
                # Atomic hardware journal replacements have no D-Bus event.
                # Poll that small file; periodic bus reconciliation covers lost signals.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(wake.wait(), 1)
        finally:
            if not preserve:
                await agent.stop()
            bus.unexport(BUS_PATH)
            bus.disconnect()


async def request(command):
    from dbus_next import Message, MessageType
    from dbus_next.aio import MessageBus
    bus = await MessageBus().connect()
    try:
        reply = await asyncio.wait_for(bus.call(Message(
            destination=BUS_NAME, path=BUS_PATH, interface=BUS_NAME,
            member='Status' if command == 'status' else 'ReleasePerformanceMode')), 15)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f'{reply.error_name}: {reply.body}')
        return json.loads(reply.body[0])
    finally:
        bus.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['daemon', 'status', 'release'], nargs='?', default='status')
    parser.add_argument('effect', nargs='?', choices=['performance-mode'])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if (args.command == 'release') != bool(args.effect):
        parser.error('release requires performance-mode; other commands take no effect')
    if args.command == 'daemon':
        config = load(os.environ.get('TURBO_AGENT_CONFIG', '@CONFIG@'), {})
        runtime = Path(os.environ['XDG_RUNTIME_DIR']) / 'turbo-agent'
        asyncio.run(serve(config, runtime))
    else:
        print(json.dumps(asyncio.run(request(args.command)), indent=None if args.json else 2, sort_keys=True))


if __name__ == '__main__':
    main()
