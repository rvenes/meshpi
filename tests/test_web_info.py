"""Read-only address discovery and login guidance; no daemon or radio."""
import json
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from meshpi.cli import main
from meshpi.config import Settings
from meshpi.web_setup import tailscale_address, web_info


@pytest.fixture
def discovery(monkeypatch):
    monkeypatch.setattr('meshpi.web_setup.shutil.which', lambda _: '/bin/tailscale')

    def respond(payload, returncode=0):
        def run(command, **options):
            assert command == ['/bin/tailscale', 'status', '--json']
            assert options['timeout'] == 3
            assert 'shell' not in options
            return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload))
        monkeypatch.setattr('meshpi.web_setup.subprocess.run', run)
    return respond


def test_discovers_only_this_machine_and_normalizes_dns(discovery):
    discovery({'BackendState': 'Running', 'Self': {'DNSName': 'Test.Example.ts.net.'},
               'Peer': {'private': 'never expose'}, 'AuthURL': 'secret'})
    assert tailscale_address() == ('https://test.example.ts.net', 'running')


@pytest.mark.parametrize('payload, state', [
    ({'BackendState': 'NeedsLogin', 'AuthURL': 'secret'}, 'inactive'),
    ({'BackendState': 'Stopped', 'Self': {'DNSName': 'old.example.ts.net'}}, 'inactive'),
    ([], 'unavailable'),
    ({'BackendState': 'Running', 'Self': None}, 'unavailable'),
    ({'BackendState': 'Running', 'Self': {'DNSName': 42}}, 'unavailable'),
    ({'BackendState': 'Running', 'Self': {'DNSName': 'evil.test'}}, 'unavailable'),
    ({'BackendState': 'Running', 'Self': {'DNSName': 'test.ts.net\n'}}, 'unavailable'),
    ({'BackendState': 'Running', 'Self': {'DNSName': 'x/evil.ts.net'}}, 'unavailable'),
    ({'BackendState': 'Running', 'Self': {'DNSName': '-x.ts.net'}}, 'unavailable'),
])
def test_invalid_and_inactive_discovery(payload, state, discovery):
    discovery(payload)
    assert tailscale_address() == ('', state)


@pytest.mark.parametrize('error', [OSError('private error'), ValueError('bad JSON'),
                                     subprocess.TimeoutExpired('tailscale', 3)])
def test_discovery_failure_is_optional(discovery, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr('meshpi.web_setup.subprocess.run', fail)
    assert tailscale_address() == ('', 'unavailable')


def test_missing_tailscale_never_runs_process(monkeypatch):
    monkeypatch.setattr('meshpi.web_setup.shutil.which', lambda _: None)
    monkeypatch.setattr('meshpi.web_setup.subprocess.run',
                        lambda *a, **k: pytest.fail('Must not start a process'))
    assert tailscale_address() == ('', 'missing')


def test_nonzero_exit_never_uses_stale_address(discovery):
    discovery({'BackendState': 'Running', 'Self': {'DNSName': 'test.example.ts.net'}}, 1)
    assert tailscale_address() == ('', 'unavailable')


@pytest.mark.parametrize('base_path', ['', '/meshpi', '/custom/web'])
def test_discovery_never_changes_canonical_address(monkeypatch, base_path):
    monkeypatch.setattr('meshpi.web_setup.tailscale_address',
                        lambda: ('https://new.example.ts.net', 'running'))
    monkeypatch.setattr('meshpi.web_setup.check_web', lambda _: True)
    settings = Settings(web_public_url='https://saved.example.ts.net',
                        web_port=8791, web_base_path=base_path)
    info = web_info(settings)
    assert info['login_url'] == f'https://saved.example.ts.net{base_path}/'
    assert info['tailscale_url'] == f'https://new.example.ts.net{base_path}/'
    assert info['local_url'] == f'http://localhost:8791{base_path}/'
    assert info['passkeys_enabled'] is True


@pytest.mark.parametrize('public_url', ['', 'https://saved.example.ts.net'])
@pytest.mark.parametrize('as_json', [False, True])
def test_cli_is_read_only_and_explains_keys(tmp_path, monkeypatch, capsys, public_url, as_json):
    env = tmp_path / 'web.env'
    original = (f'WEB_PUBLIC_URL={public_url}\nBACKGROUND_MODE=session\n'
                f'DATABASE_PATH={tmp_path}/state/db\nIPC_TOKEN=private-test-token\n')
    env.write_text(original)
    def forbidden(*args, **kwargs):
        pytest.fail('Information must not contact/start a daemon or issue a code')
    monkeypatch.setattr('meshpi.cli.start_session_daemon', forbidden)
    monkeypatch.setattr('meshpi.cli.manage_service', forbidden)
    monkeypatch.setattr('meshpi.cli._request', forbidden)
    monkeypatch.setattr('meshpi.web_auth.AuthStore.issue_code', forbidden)
    monkeypatch.setattr('meshpi.web_setup.tailscale_address',
                        lambda: ('https://detected.example.ts.net', 'running'))
    monkeypatch.setattr('meshpi.web_setup.check_web', lambda _: False)
    main(['--env-file', str(env), *(['--json'] if as_json else []), 'web-info'])
    output = capsys.readouterr().out
    assert 'private-test-token' not in output
    assert env.read_text() == original
    assert not (tmp_path / 'state').exists()
    if as_json:
        data = json.loads(output)
        assert data['passkeys_enabled'] is bool(public_url)
        assert data['responding_locally'] is False
        assert data['enabled'] is False
    else:
        assert 'web-code' in output and '--local' in output
        assert 'meshpi web-code' not in output
        assert str(env) in output
        assert '5 minutt' in output
        assert 'https://detected.example.ts.net/meshpi/' in output
        if public_url:
            assert 'Legg til passkey' in output
            assert 'ulik den lagra' in output
        else:
            assert 'ikkje automatisk aktivert' in output


def test_displayed_command_uses_running_install_and_quotes_config(tmp_path, monkeypatch, capsys):
    env = tmp_path / "web's config $(echo unsafe).env"
    env.write_text('')
    monkeypatch.setattr('meshpi.web_setup.tailscale_address', lambda: ('', 'missing'))
    monkeypatch.setattr('meshpi.web_setup.check_web', lambda _: True)
    main(['--env-file', str(env), 'web-info', '--local'])
    output = capsys.readouterr().out
    command = output.split('serveren:\n\n  ', 1)[1].split('\n\n', 1)[0]
    assert shlex.split(command.replace('\\\n', '')) == [
        sys.executable, '-m', 'meshpi', '--env-file', str(env), 'web-code', '--local',
    ]
    assert '\n\nOPNE I NETTLESAREN\n' in output
    assert '\n\nFØRSTE INNLOGGING\n' in output
    assert '\n\nMISTA NØKKELEN?\n' in output


@pytest.mark.parametrize('platform, mode, sudo', [
    ('linux', 'always', True), ('linux', 'session', False),
    ('darwin', 'always', False),
])
@pytest.mark.parametrize('base_path', ['', '/meshpi', '/custom/web'])
def test_setup_commands_use_current_config_address_port_and_mode(tmp_path, platform, mode,
                                                                 sudo, base_path):
    from meshpi.web_setup import setup_guidance

    env = tmp_path / "web's config $(echo unsafe).env"
    settings = Settings(web_port=8791, web_base_path=base_path, background_mode=mode)
    info = {'tailscale_url': f'https://device.example.ts.net{base_path}/', 'public_url': ''}
    output = setup_guidance(settings, env, info, executable='/opt/mesh pi/python',
                            platform=platform)
    commands = [shlex.split(line.strip()) for line in output.splitlines()
                if line.startswith('  ')]
    prefix = (['sudo'] if sudo else []) + ['/opt/mesh pi/python', '-m', 'meshpi',
                                         '--env-file', str(env)]
    assert prefix + ['web-configure', '--enable', '--public-url',
                     'https://device.example.ts.net', '--base-path', base_path] in commands
    assert ['sudo', 'tailscale', 'serve', '--bg', '--set-path', base_path or '/',
            'http://127.0.0.1:8791'] in commands
    if mode == 'session':
        assert prefix + ['web'] in commands
        assert not any('service' in command for command in commands)
    else:
        for action in ('enable', 'stop', 'start'):
            assert prefix + ['service', action] in commands
    assert 'No serve config' in output and '--bg' in output


def test_windows_setup_quotes_literal_config_and_never_prints_sudo(tmp_path):
    from meshpi.web_setup import setup_guidance

    env = tmp_path / "user's config.env"
    output = setup_guidance(Settings(background_mode='always'), env,
                            {'tailscale_url': 'https://device.example.ts.net/meshpi/',
                             'public_url': ''},
                            executable='C:\\Program Files\\MeshPi\\python.exe', platform='win32')
    assert "& 'C:\\Program Files\\MeshPi\\python.exe' '-m' 'meshpi' '--env-file'" in output
    assert str(env).replace("'", "''") in output
    assert "& 'tailscale' 'serve' '--bg' '--set-path' '/meshpi'" in output
    assert 'sudo' not in output and '&&' not in output


def test_setup_does_not_replace_saved_origin_on_mismatch():
    from meshpi.web_setup import setup_guidance

    output = setup_guidance(Settings(web_public_url='https://saved.example.ts.net'), 'web.env',
                            {'tailscale_url': 'https://other.example.ts.net/meshpi/',
                             'public_url': 'https://saved.example.ts.net/meshpi/'},
                            executable='python', platform='linux')
    assert 'web-configure' not in output and 'serve --bg' not in output
    assert 'ulike' in output


def test_missing_tailscale_shows_local_enable_without_inventing_address():
    from meshpi.web_setup import setup_guidance

    output = setup_guidance(Settings(background_mode='session'), 'web.env',
                            {'tailscale_url': '', 'public_url': ''},
                            executable='python', platform='linux')
    assert 'web-configure --enable' in output
    assert '--public-url' not in output and 'serve --bg' not in output
    assert 'installer Tailscale og logg inn' in output
