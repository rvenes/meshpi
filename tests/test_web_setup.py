"""Installer-facing web settings and local IPC recovery; no radio traffic."""
import json
import os
from dataclasses import replace

import pytest

from meshpi.cli import main
from meshpi.config import Settings
from meshpi.events import EventHub
from meshpi.ipc import IPCApplication
from meshpi.lifecycle import DaemonHandle
from meshpi.web_setup import check_web, configure_web, web_settings


def test_configure_preserves_private_existing_config_and_defaults(tmp_path):
    path = tmp_path / 'meshpi.env'
    path.write_text('# private config\nIPC_TOKEN=keep-this-token\nDATABASE_PATH=data/custom.db\n')
    path.chmod(0o600)
    before = Settings.load(path)
    configured = configure_web(path, before, enabled=True, port=8781,
                               public_url='https://test.example.ts.net/')
    loaded = Settings.load(path)
    assert loaded == configured
    assert loaded.web_origin == 'https://test.example.ts.net'
    assert loaded.web_auth_dir == loaded.database_path.parent / 'web-auth'
    assert loaded.ipc_token == 'keep-this-token'
    assert path.read_text().startswith('# private config\n')
    if os.name == 'posix':
        assert path.stat().st_mode & 0o777 == 0o600
    configure_web(path, loaded, enabled=False)
    assert not Settings.load(path).web_enabled
    assert Settings.load(path).web_public_url == loaded.web_public_url


@pytest.mark.parametrize('values', [
    {'port': 0}, {'port': 65536}, {'base_path': '/../../x'},
    {'base_path': '/x\nWEB_ENABLED=true'}, {'public_url': 'http://test.example.ts.net'},
    {'public_url': 'https://test.example.ts.net/path'}, {'public_url': 'https://example.org'},
    {'public_url': 'https://user:secret@test.example.ts.net'},
    {'public_url': 'https://test.example.ts.net:444'},
])
def test_bad_configuration_never_modifies_existing_file(tmp_path, values):
    path = tmp_path / 'meshpi.env'
    original = 'IPC_TOKEN=preserved\n'
    path.write_text(original)
    with pytest.raises(ValueError):
        configure_web(path, Settings(), **values)
    assert path.read_text() == original


def test_configure_rejects_symlink(tmp_path):
    target = tmp_path / 'target'
    target.write_text('preserved')
    path = tmp_path / 'config'
    path.symlink_to(target)
    with pytest.raises(ValueError):
        configure_web(path, Settings())
    assert target.read_text() == 'preserved'


def test_web_configure_and_health_never_start_daemon(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('No daemon or radio may start for configuration/health')
    monkeypatch.setattr('meshpi.cli.start_session_daemon', forbidden)
    monkeypatch.setattr('meshpi.cli.manage_service', forbidden)
    monkeypatch.setattr('meshpi.web_setup.check_web', lambda _: True)
    path = tmp_path / 'meshpi.env'
    main(['--env-file', str(path), 'web-configure', '--enable'])
    main(['--env-file', str(path), 'web-check'])
    assert Settings.load(path).web_enabled


@pytest.mark.parametrize('owned', [True, False])
def test_web_session_lifecycle_only_stops_owned_daemon(tmp_path, monkeypatch, owned):
    path = tmp_path / 'meshpi.env'
    path.write_text('BACKGROUND_MODE=session\nWEB_PORT=8791\n')
    calls = []
    monkeypatch.setattr('meshpi.cli.start_session_daemon',
                        lambda *_: DaemonHandle(owned=owned))
    monkeypatch.setattr('meshpi.cli.stop_daemon', lambda _: calls.append('stop'))
    monkeypatch.setattr('meshpi.web.run_web', lambda settings, **kw: calls.append(kw))
    main(['--env-file', str(path), 'web'])
    assert calls[0]['port'] == 8791
    assert calls[1:] == (['stop'] if owned else [])


def test_disabled_managed_web_does_not_start_daemon(tmp_path, monkeypatch):
    path = tmp_path / 'meshpi.env'
    path.write_text('BACKGROUND_MODE=session\nWEB_ENABLED=false\n')
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled managed web must not start a process')
    monkeypatch.setattr('meshpi.cli.start_session_daemon', forbidden)
    monkeypatch.setattr('meshpi.web.run_web', forbidden)
    main(['--env-file', str(path), 'web', '--managed'])


def test_local_ipc_code_supports_new_port_without_radio_or_database(tmp_path):
    settings = Settings(database_path=tmp_path/'db')
    app = IPCApplication(settings, None, None, EventHub())
    result = app.dispatch({'command': 'web_login_code', 'origin': 'http://localhost:8791'})
    assert result['ok']
    assert len(result['data']['code']) >= 20
    assert not settings.database_path.exists()
    state = json.loads((settings.web_auth_dir/'auth.json').read_text())
    assert state['origin'] == 'http://localhost:8791'
    assert result['data']['code'] not in json.dumps(state)
    with pytest.raises(ValueError):
        app.dispatch({'command': 'web_login_code', 'origin': 'http://evil.test:8791'})


def test_web_settings_uses_saved_defaults_and_explicit_empty_url():
    settings = Settings(web_enabled=True, web_port=8791, web_public_url='https://test.example.ts.net')
    assert web_settings(settings) == settings
    assert web_settings(settings, public_url='') == replace(settings, web_public_url='')


def test_health_failure_has_no_implicit_start(monkeypatch):
    monkeypatch.setattr('meshpi.web_setup.HTTPConnection', lambda *a, **kw: BrokenConnection())
    assert not check_web(Settings())


class BrokenConnection:
    def request(self, *args):
        raise ConnectionRefusedError

    def close(self):
        pass


def test_offline_web_code_never_starts_daemon_and_keeps_code_out_of_state(
    tmp_path, monkeypatch, capsys,
):
    env = tmp_path / 'meshpi.env'
    env.write_text(f'DATABASE_PATH={tmp_path}/data/db\nBACKGROUND_MODE=session\n')
    def forbidden(*args, **kwargs):
        pytest.fail('Offline recovery must not start or contact a daemon')
    monkeypatch.setattr('meshpi.cli.start_session_daemon', forbidden)
    monkeypatch.setattr('meshpi.cli._request', forbidden)
    main(['--env-file', str(env), 'web-code', '--local'])
    output = capsys.readouterr().out
    code = output.splitlines()[1]
    assert len(code) >= 20
    assert code not in (tmp_path/'data/web-auth/auth.json').read_text()


def test_default_web_code_uses_local_ipc(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr('meshpi.cli.daemon_status', lambda _: {})
    monkeypatch.setattr('meshpi.cli._request', lambda _, payload:
                        calls.append(payload) or {'data': {'code': 'synthetic-test-code'}})
    main(['--env-file', str(tmp_path/'absent.env'), 'web-code'])
    assert calls == [{'command': 'web_login_code', 'origin': 'http://localhost:8780'}]
    assert 'synthetic-test-code' in capsys.readouterr().out
