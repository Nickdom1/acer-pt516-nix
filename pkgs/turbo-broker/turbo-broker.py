"""Serial owner of Predator Turbo add-ons. See docs/architecture.md."""
import argparse
import array
import contextlib
import fcntl
import grp
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import sys
import time

PROFILE = Path('/sys/firmware/acpi/platform_profile')
RUNTIME = Path('/run/turbo-broker')
CONFIG = Path(os.environ.get('TURBO_BROKER_CONFIG', '/etc/turbo-broker.json'))
LIGHTING_TIMEOUT = Path('/sys/devices/platform/acer-wmi/kbd_backlight_timeout')
POWER_SUPPLY = Path('/sys/class/power_supply')
STATUS_GROUP = 'turbo-broker-status'
# A game profile request is leased to the requesting process. The grace
# period covers a healthy agent restart (RestartSec=2 plus start-up, or one
# of its 10-second bus timeouts) so a yielded request survives it; an owner
# gone beyond it releases the request.
LEASE_GRACE_SECONDS = 30


def read(path):
    return Path(path).read_text().strip()


def write(path, value):
    Path(path).write_text(str(value))


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def save(path, data):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as stream:
        json.dump(data, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.chmod(0o640)
    tmp.replace(path)


def zones():
    return sorted(Path('/sys/class/leds').glob('acer-wmi:rgb:kbd_zoned_backlight-*'))


def fans():
    for hw in Path('/sys/class/hwmon').glob('hwmon*'):
        if read(hw / 'name') == 'acer':
            return sorted(hw.glob('pwm*_enable'))
    return []


def battery():
    supplies = list(POWER_SUPPLY.glob('*'))
    # Only line-power supplies say whether the laptop is plugged in; a
    # peripheral's battery (HID mouse, gamepad) reports online=1 regardless.
    online = [read(p / 'online') == '1' for p in supplies
              if (p / 'online').exists() and not ((p / 'type').exists() and read(p / 'type') == 'Battery')]
    if online:
        return not any(online)
    return any(read(p / 'status') == 'Discharging' for p in supplies if (p / 'status').exists())


def now():
    # Monotonic: survives daemon restarts within a boot and, unlike
    # CLOCK_BOOTTIME, does not count a suspend against the grace period.
    return time.monotonic()


def parse_stat(pid, stat):
    """Identity from /proc/<pid>/stat bytes; None for a dead or zombie process."""
    try:
        # The command name is raw bytes and may hold spaces or parentheses;
        # the state and the remaining fields follow the last ')'.
        fields = stat[stat.rfind(b')') + 2:].split()
        if fields[0] in (b'Z', b'X'):
            return None
        return {'pid': pid, 'start': int(fields[19])}  # starttime is field 22
    except (IndexError, ValueError):
        return None


def process_identity(pid):
    """Stable identity of a live process: pid plus kernel start time."""
    if not pid:
        return None
    try:
        return parse_stat(pid, Path(f'/proc/{pid}/stat').read_bytes())
    except OSError:
        return None


def lease_alive(owner):
    return owner is not None and process_identity(owner['pid']) == owner


def read_timeout(path=LIGHTING_TIMEOUT):
    value = int(read(path))
    if not 0 <= value <= 255:
        raise ValueError('keyboard idle timeout outside 0-255 seconds')
    return value


def write_timeout(path, value):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255:
        raise ValueError('keyboard idle timeout must be 0-255 seconds')
    write(path, value)
    if read_timeout(path) != value:
        raise RuntimeError('keyboard idle timeout readback mismatch')


def reset_boot(runtime=RUNTIME, boot_id_path=Path('/proc/sys/kernel/random/boot_id')):
    """Boot policy only. A marker prevents same-boot restarts resetting Turbo."""
    import subprocess
    runtime.mkdir(exist_ok=True)
    marker = runtime / 'boot-policy.json'
    boot_id = read(boot_id_path)
    if load(marker, {}).get('bootId') == boot_id:
        return
    with (runtime / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths = fans()
        if not paths:
            raise RuntimeError('Acer fan controls missing; boot policy not applied')
        write(PROFILE, 'balanced')
        # Type=dbus readiness precedes ppd's saved-profile restore. Its setter
        # runs after that restore and persists balanced for subsequent boots.
        # The first sysfs write is still needed to clear a retained key latch.
        try:
            subprocess.run(['powerprofilesctl', 'set', 'balanced'], check=True,
                           capture_output=True, text=True, timeout=15)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f'Boot reset: ppd unavailable ({type(exc).__name__}); using sysfs only', file=sys.stderr)
            write(PROFILE, 'balanced')
        for path in paths:
            write(path, '2')
        if read(PROFILE) != 'balanced' or any(read(path) != '2' for path in paths):
            raise RuntimeError('Boot policy readback failed')
        save(marker, {'bootId': boot_id})


class Broker:
    def __init__(self, config, runtime=RUNTIME):
        self.cfg = config
        self.runtime = runtime
        self.path = runtime / 'state.json'
        self.state = load(self.path, {'version': 4, 'phase': 'off', 'saved': {}, 'applied': []})
        if self.state.get('version') not in (1, 2, 3, 4):
            raise ValueError('unknown state version; refusing to discard restore data')
        # Older brokers must reject game-profile restore records they do not
        # understand; retain existing add-on snapshots through this upgrade.
        if set(self.state['saved']) - {'lighting', 'lightingTimeout'}:
            raise ValueError('retired add-on restore data present; release with the previous broker first')
        self.state['version'] = 4
        self.stopping = None

    def persist(self):
        save(self.path, self.state)

    def snapshot(self, name):
        if name == 'lightingTimeout':
            return {'path': str(LIGHTING_TIMEOUT), 'seconds': read_timeout(LIGHTING_TIMEOUT)}
        if name == 'lighting':
            if not zones():
                raise RuntimeError('RGB LED class devices missing')
            return {str(z): {'color': read(z / 'multi_intensity'),
                             'brightness': read(z / 'brightness')} for z in zones()}
        raise ValueError('unknown hardware effect: ' + name)

    def apply(self, name):
        cfg = self.cfg['addons']['lighting' if name == 'lightingTimeout' else name]
        if name == 'lightingTimeout':
            write_timeout(self.state['saved'][name]['path'], cfg['idleTimeoutSeconds'])
        elif name == 'lighting':
            for z in self.state['saved'][name]:
                write(Path(z) / 'multi_intensity', cfg['color'])
                write(Path(z) / 'brightness', cfg['brightness'])
        return True

    def restore(self, name):
        saved = self.state['saved'][name]
        if name == 'lightingTimeout':
            # stayOn retains colour only, never the firmware idle policy.
            write_timeout(saved['path'], saved['seconds'])
        elif name == 'lighting':
            if not self.state.get('stayOn', False):
                for zone, value in saved.items():
                    write(Path(zone) / 'multi_intensity', value['color'])
                    write(Path(zone) / 'brightness', value['brightness'])

    def release(self, names):
        if not names:
            return
        self.state['phase'] = 'leaving'
        self.persist()
        # On failure retain all outstanding restore data, including an apply
        # interrupted after its write but before its completion marker.
        errors = []
        for name in reversed(names):
            try:
                self.restore(name)
                del self.state['saved'][name]
                if name in self.state['applied']:
                    self.state['applied'].remove(name)
                self.persist()
            except Exception as exc:
                errors.append(f'{name} restore: {exc}')
        if errors:
            raise RuntimeError('; '.join(errors))

    def leave(self):
        self.release(list(self.state['saved']))
        self.state['phase'] = 'off'
        self.persist()

    def game_active(self):
        return self.cfg['games']['enable'] and self.state.get('gameProfile', {}).get('requested', False)

    def desired(self, profile, on_battery, game, max_fans):
        # skip-addons releases the add-ons on battery; end-turbo also ends
        # Turbo itself (see end_turbo), so neither applies any on battery.
        if self.cfg['onBattery'] in ('skip-addons', 'end-turbo') and on_battery:
            return set(), 'battery'
        if profile == 'performance' and max_fans:
            return {n for n, c in self.cfg['addons'].items() if n == 'lighting' and c['enable']}, 'turbo'
        if game:
            # The session agent owns desktop holds; hardware owns lighting.
            return {
                n for n in ('lighting',)
                if self.cfg['addons'].get(n, {}).get('enable', False) and self.cfg['games']['addons'].get(n, False)
            }, 'game'
        if profile == 'performance':
            return {n for n in self.cfg.get('profileAddons', [])
                    if n == 'lighting' and self.cfg['addons'].get(n, {}).get('enable', False)}, 'profile'
        return set(), 'off'

    def expire_lease(self):
        """Release a request whose owner has been gone beyond the grace period.

        Returns True when a request was released."""
        request = self.state.get('gameProfile')
        if not request or not request.get('requested') or 'owner' not in request:
            return False
        if lease_alive(request['owner']):
            if request.pop('ownerLostSince', None) is not None:
                print(f"game profile owner {request['owner']['pid']} is back", flush=True)
                self.persist()
            return False
        if 'ownerLostSince' not in request:
            request['ownerLostSince'] = now()
            print(f"game profile owner {request['owner']['pid']} gone; "
                  f'releasing in {LEASE_GRACE_SECONDS}s unless re-requested', flush=True)
            self.persist()
            return False
        if now() - request['ownerLostSince'] < LEASE_GRACE_SECONDS:
            return False
        print(f"game profile owner {request['owner']['pid']} gone; releasing its request", flush=True)
        self.game_profile(False)
        return True

    def reconcile(self, profile, on_battery, game=False, max_fans=False):
        if self.expire_lease():
            # The caller sampled the request and profile before the release.
            game, profile = False, read(PROFILE)
        if 'gameProfile' in self.state:
            if not game:
                self.game_profile(False)
            else:
                if not self.cfg['games'].get('performanceProfile', False) and 'restore' in self.state['gameProfile']:
                    owner = self.state['gameProfile'].get('owner')
                    self.game_profile(False)
                    self.game_profile(True, owner)
                self.observe_game_profile()
            # A restore may have changed the profile in this transaction.
            profile = read(PROFILE)
        old = self.state.get('observed', {})
        turbo = profile == 'performance' and max_fans
        observed = {'available': True, 'profile': profile, 'onBattery': on_battery,
                    'turbo': turbo, 'entry': old.get('entry', 0) + int(turbo and not old.get('turbo'))}
        if observed != old:
            self.state['observed'] = observed
            self.persist()
        unended = self.state.get('batteryEnded') != observed['entry']
        if self.cfg['onBattery'] == 'end-turbo' and on_battery and turbo and unended:
            profile = self.end_turbo(observed['entry'])
        wanted, mode = self.desired(profile, on_battery, game, max_fans)
        if 'lighting' in wanted and self.cfg['addons']['lighting'].get('idleTimeoutSeconds') is not None:
            wanted.add('lightingTimeout')
        if profile != 'performance' and 'hardware' in self.state:
            # External changes invalidate a CLI snapshot; never resurrect it.
            del self.state['hardware']
            self.persist()
        # Save/apply each add-on independently, including transitions between
        # a game's subset and the full Turbo set. Retry only unwanted restores;
        # a failed release must not rewrite add-ons the game still owns.
        self.state['mode'] = mode
        self.release([name for name in self.state['saved'] if name not in wanted])
        if wanted == set(self.state['applied']):
            phase = 'on' if wanted else 'off'
            if self.state['phase'] != phase or self.state.get('lastMode') != mode:
                self.state.update(phase=phase, lastMode=mode)
                self.persist()
            return
        self.state['phase'] = 'entering'
        self.persist()
        errors = []
        for name in ('lighting', 'lightingTimeout'):
            if name not in wanted or name in self.state['applied']:
                continue
            try:
                if name not in self.state['saved']:
                    self.state['saved'][name] = self.snapshot(name)
                    if name == 'lighting':
                        self.state['stayOn'] = self.cfg['addons']['lighting']['stayOn']
                    self.persist()
                if self.apply(name):
                    self.state['applied'].append(name)
                    self.persist()
            except Exception as exc:
                errors.append(f'{name} apply: {exc}')
        if wanted == set(self.state['applied']):
            self.state['phase'] = 'on'
        self.persist()
        if errors:
            raise RuntimeError('; '.join(errors))

    def end_turbo(self, entry):
        """onBattery = end-turbo: end this Turbo entry once, as boot-reset does.

        Selecting another profile through power-profiles-daemon makes the
        kernel driver end key Turbo, fans included. The entry is recorded
        before the request so a failure or restart never repeats it; a later
        key press is a new entry. Fans are written only to undo the broker's
        own CLI Turbo, from its snapshot. Returns the profile now reported."""
        import subprocess
        self.state['batteryEnded'] = entry
        self.persist()
        print(f'on battery with onBattery=end-turbo: ending Turbo entry {entry} '
              'by requesting balanced through power-profiles-daemon', flush=True)
        try:
            subprocess.run(['powerprofilesctl', 'set', 'balanced'], check=True,
                           capture_output=True, text=True, timeout=15)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f'end-turbo: ppd unavailable ({type(exc).__name__}); writing the balanced profile through sysfs',
                  file=sys.stderr, flush=True)
            write(PROFILE, 'balanced')
        if read(PROFILE) == 'performance':
            print('end-turbo: profile still performance after the ppd request; writing balanced through sysfs',
                  file=sys.stderr, flush=True)
            write(PROFILE, 'balanced')
        hardware = self.state.pop('hardware', None)
        if hardware is not None:
            # CLI Turbo: the broker wrote the fan modes, so it restores them.
            for path in fans():
                if read(path) == '0':
                    write(path, hardware.get('fans', {}).get(str(path), '2'))
            self.persist()
        profile = read(PROFILE)
        modes = [read(path) for path in fans()]
        if profile == 'performance' or '0' in modes:
            print(f'end-turbo: Turbo did not end (profile {profile}, fan modes {modes})', file=sys.stderr, flush=True)
        else:
            print(f'end-turbo: Turbo ended (profile {profile}, fan modes {modes}); '
                  'it re-enters only on the next key press', flush=True)
        return profile

    def profile_state(self):
        paths = fans()
        if not paths:
            raise RuntimeError('Acer fan controls missing')
        modes = [read(p) for p in paths]
        if any(mode not in ('0', '1', '2') for mode in modes):
            raise RuntimeError('Unknown fan mode; retaining game profile ownership')
        return read(PROFILE), any(mode == '0' for mode in modes)

    def observe_game_profile(self):
        request = self.state.get('gameProfile')
        if not request:
            return
        if not request['requested']:
            self.game_profile(False)
            return
        profile, turbo_fans = self.profile_state()
        if turbo_fans or (profile != 'performance' and request['phase'] != 'entering'):
            # Keep the request lifetime: a repeat on/restart must not reapply
            # performance after the key or an external profile took ownership.
            if 'restore' in request or request['phase'] != 'yielded':
                request.pop('restore', None)
                request['phase'] = 'yielded'
                self.persist()
        elif request['phase'] == 'entering':
            old = request.get('restore')
            if profile not in (old, 'performance'):
                request.pop('restore', None)
                request['phase'] = 'yielded'
            else:
                if profile != 'performance':
                    write(PROFILE, 'performance')
                request['phase'] = 'owned'
            self.persist()

    def game_profile(self, on, owner=None):
        request = self.state.get('gameProfile')
        if on:
            if not self.cfg['games']['enable']:
                raise ValueError('Game performance profile requests are disabled')
            if request and not request['requested']:
                self.game_profile(False)
                request = None
            if request:
                # A repeat request from a new sender takes the lease only from
                # a lost or absent owner, keeping the phase (yielded stays
                # yielded); a live owner's lease is never taken from it.
                current = request.get('owner')
                if owner is not None and current != owner and not lease_alive(current):
                    print(f"game profile lease moves to {owner['pid']}", flush=True)
                    request['owner'] = owner
                    request.pop('ownerLostSince', None)
                    self.persist()
                self.observe_game_profile()
                return
            profile, turbo_fans = self.profile_state()
            request = {'requested': True, 'phase': 'yielded'}
            if owner is not None:
                request['owner'] = owner
            if self.cfg['games'].get('performanceProfile', False) and not turbo_fans and profile != 'performance':
                request.update(phase='entering', restore=profile)
            self.state['gameProfile'] = request
            self.persist()  # Original profile is durable before the write.
            self.observe_game_profile()
        elif request:
            request['requested'] = False
            self.persist()
            profile, turbo_fans = self.profile_state()
            if 'restore' in request and profile == 'performance' and not turbo_fans:
                write(PROFILE, request['restore'])
            del self.state['gameProfile']
            self.persist()

    def set_turbo(self, on):
        paths = fans()
        if not paths:
            raise RuntimeError('Acer fan controls missing')
        if on:
            if 'hardware' not in self.state:
                old = read(PROFILE)
                self.state['hardware'] = {
                    'profile': old if old != 'performance' else 'balanced',
                    'fans': {str(p): read(p) if read(p) != '0' else '2' for p in paths}}
                self.persist()
            write(PROFILE, 'performance')
            for path in paths:
                write(path, 0)
            self.observe_game_profile()
        else:
            saved = self.state.get('hardware', {})
            if read(PROFILE) == 'performance':
                write(PROFILE, saved.get('profile', 'balanced'))
            for path in paths:
                write(path, saved.get('fans', {}).get(str(path), '2'))
            self.state.pop('hardware', None)
            self.persist()

    def signal(self, signum, frame):
        # Do not interrupt an in-flight write/record pair.
        self.stopping = 'restart' if signum == signal.SIGUSR1 else 'stop'

    def stop_effects(self):
        self.state['observed'] = dict(self.state.get('observed', {}), available=False)
        self.persist()
        try:
            if self.state.get('gameProfile', {}).get('phase') != 'entering':
                self.observe_game_profile()
            # A yielded request still records the game's lifetime. Retain it
            # so a reconnect cannot undo the user's later profile selection.
            if 'restore' in self.state.get('gameProfile', {}):
                self.game_profile(False)
        finally:
            self.leave()  # Physical/CLI Turbo remains untouched.

    def command(self, command, uid, groups, pid=None):
        if uid != 0:
            if grp.getgrnam(STATUS_GROUP).gr_gid not in groups:
                raise PermissionError('Turbo status group required')
            if command not in ('game-profile on', 'game-profile off'):
                raise PermissionError('Only root may change software Turbo')
        if command in ('on', 'off'):
            self.set_turbo(command == 'on')
        elif command == 'game-profile on':
            # Leased to the sender: the long-lived agent, or a CLI process
            # whose request lapses with it after the grace period.
            owner = process_identity(pid) if pid is not None else None
            if pid is not None and owner is None:
                raise RuntimeError('requesting process already gone; no lease taken')
            self.game_profile(True, owner)
        elif command == 'game-profile off':
            self.game_profile(False)
        else:
            raise ValueError('Unknown broker command')
        return {'ok': True}

    def run(self):
        self.runtime.mkdir(exist_ok=True)
        with (self.runtime / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
                signal.signal(sig, self.signal)
            endpoint = self.runtime / 'control'
            endpoint.unlink(missing_ok=True)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(endpoint))
                endpoint.chmod(0o660)
                server.listen(8)
                try:
                    self.loop(server)
                finally:
                    endpoint.unlink(missing_ok=True)
                if self.stopping == 'stop':
                    self.stop_effects()

    def loop(self, server):
        previous, since = None, time.monotonic()
        poller = select.poll()
        poller.register(server, select.POLLIN)
        # Keep the FD open and seek/read to acknowledge sysfs POLLPRI.
        with PROFILE.open() as profile_file:
            poller.register(profile_file, select.POLLPRI | select.POLLERR)
            while not self.stopping:
                profile_file.seek(0)
                profile = profile_file.read().strip()
                fan_modes = [read(p) for p in fans()]
                current = (profile, battery(), self.game_active(),
                           bool(fan_modes) and all(mode == '0' for mode in fan_modes))
                if current != previous:
                    previous, since = current, time.monotonic()
                if time.monotonic() - since >= 0.25:
                    try:
                        self.reconcile(*current)
                        if self.state.pop('error', None):
                            self.persist()
                    except Exception as exc:
                        message = str(exc)
                        if self.state.get('error') != message:
                            print(f'reconcile failed (will retry): {message}', flush=True)
                            self.state['error'] = message
                            self.persist()
                # AC/DC is checked every second.
                for fd, _event in poller.poll(250 if time.monotonic() - since < 0.25 else 1000):
                    if fd != server.fileno():
                        continue
                    with server.accept()[0] as client:
                        client.settimeout(2)
                        try:
                            pid, uid, _ = struct.unpack(
                                '3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                            )
                            groups = array.array('i')
                            if uid != 0:
                                # Linux SO_PEERGROUPS; fail closed on unavailable
                                # or oversized results (Python limits buflen).
                                groups.frombytes(client.getsockopt(
                                    socket.SOL_SOCKET, getattr(socket, 'SO_PEERGROUPS', 59), 1024
                                ))
                            command = client.recv(65)
                            if len(command) > 64:
                                raise ValueError('Broker command too long')
                            reply = self.command(command.decode().strip(), uid, groups, pid)
                        except Exception as exc:
                            reply = {'ok': False, 'error': str(exc)}
                        with contextlib.suppress(BrokenPipeError):
                            client.sendall(json.dumps(reply).encode())


def daemon_running(runtime):
    """Observe daemon lifetime; a held lock does not prove responsiveness."""
    try:
        if not (runtime / 'control').is_socket():
            return False
        info = (runtime / 'lock').stat()
        identity = f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}'
        # Read kernel lock state without briefly locking out a daemon start.
        with Path('/proc/locks').open() as locks:
            return any(fields[1:4] == ['FLOCK', 'ADVISORY', 'WRITE'] and fields[5] == identity
                       for line in locks if len(fields := line.split()) >= 8)
    except FileNotFoundError:
        return False


def status(config):
    state = load(RUNTIME / 'state.json', {'phase': 'off', 'saved': {}, 'applied': []})
    lighting = {str(z): {
        'color': read(z / 'multi_intensity'),
        'brightness': read(z / 'brightness'),
    } for z in zones()}
    timeout = None
    if LIGHTING_TIMEOUT.exists():
        try:
            timeout = {'seconds': read_timeout(LIGHTING_TIMEOUT)}
        except (OSError, ValueError) as exc:
            timeout = {'error': str(exc)}
    return {'profile': read(PROFILE), 'onBattery': battery(), 'config': config,
            'broker': state, 'running': daemon_running(RUNTIME),
            'fans': {str(p): read(p) for p in fans()}, 'lighting': lighting,
            'lightingIdleTimeout': timeout}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='status',
                        choices=['daemon', 'status', 'on', 'off', 'game-profile', 'boot-reset'])
    parser.add_argument('action', nargs='?', choices=['on', 'off'])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if (args.command == 'game-profile') != (args.action is not None):
        parser.error('game-profile requires on or off; other commands take no action')
    if args.command == 'boot-reset':
        if os.geteuid() != 0:
            parser.error('boot-reset requires root')
        reset_boot()
        return
    cfg = load(CONFIG, None)
    if cfg is None:
        parser.error(f'{CONFIG} missing')
    if args.command == 'daemon':
        Broker(cfg).run()
    elif args.command == 'status':
        print(json.dumps(status(cfg), indent=None if args.json else 2, sort_keys=True))
    else:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(340)
            client.connect(str(RUNTIME / 'control'))
            client.sendall((args.command + (' ' + args.action if args.action else '')).encode())
            result = json.loads(client.recv(4096))
        if not result['ok']:
            sys.exit(result['error'])
        if args.command == 'game-profile' and args.action == 'on':
            print(f'note: this request is leased to this process and lapses about '
                  f'{LEASE_GRACE_SECONDS}s after it exits; the session agent is the holder', file=sys.stderr)


if __name__ == '__main__':
    main()
