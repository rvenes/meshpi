"""Request-scoped web translations must not alter data or security checks."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_web import http
from test_web import web as source_web

from meshpi.i18n import get_language, using_language
from meshpi.web import _daemon_result, _daemon_text
from meshpi.web_auth import AuthError, canonical_origin


@pytest.fixture
def localized_web(monkeypatch, tmp_path):
    yield from source_web.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize(('language', 'expected'), [
    ('nb', 'Forespørselen har ukjente felt.'),
    ('nn', 'Førespurnaden har ukjende felt.'),
    ('en', 'The request has unknown fields.'),
])
def test_http_language_localizes_validation_without_forwarding_extra_fields(
    localized_web, language, expected,
):
    server, calls = localized_web
    status, headers, raw = http(server, payload={'command': 'status', 'extra': True},
                                headers={'X-MeshPi-Language': language})
    assert status == 400
    assert json.loads(raw)['error'] == expected
    assert headers['Content-Language'] == language
    assert calls == []


def test_concurrent_http_languages_reach_ipc_with_separate_contexts(localized_web, monkeypatch):
    server, _ = localized_web

    def request(settings, payload, **kwargs):
        return {'ok': True, 'data': {'language': get_language(), 'name': 'Ikkje translate me'}}

    monkeypatch.setattr('meshpi.web.request', request)
    languages = ['nb', 'nn', 'en'] * 4

    def check(language):
        status, _, raw = http(server, payload={'command': 'status'},
                              headers={'X-MeshPi-Language': language})
        assert status == 200
        return json.loads(raw)['data']

    with ThreadPoolExecutor(max_workers=6) as pool:
        data = list(pool.map(check, languages))
    assert [result['language'] for result in data] == languages
    assert all(result['name'] == 'Ikkje translate me' for result in data)


@pytest.mark.parametrize('language', ['nb', 'nn', 'en'])
def test_manifest_language_is_browser_specific_and_does_not_change_app_identity(
    localized_web, language,
):
    server, _ = localized_web
    status, headers, raw = http(server, '/meshpi/manifest.webmanifest?language=' + language)
    manifest = json.loads(raw)
    assert status == 200
    assert manifest['lang'] == language
    assert headers['Content-Language'] == language
    assert manifest['id'] == '/meshpi/'
    assert manifest['start_url'] == '/meshpi/'


@pytest.mark.parametrize('language', ['invalid', '../../en', 'nb,en'])
def test_invalid_language_headers_keep_existing_nynorsk_default(localized_web, language):
    server, _ = localized_web
    status, headers, raw = http(server, payload={'command': 'status', 'bad': True},
                                headers={'X-MeshPi-Language': language})
    assert status == 400
    assert headers['Content-Language'] == 'nn'
    assert json.loads(raw)['error'] == 'Førespurnaden har ukjende felt.'


def test_language_header_does_not_bypass_csrf(localized_web):
    server, calls = localized_web
    status, _, raw = http(server, payload={'command': 'status'},
                          headers={'X-MeshPi-Language': 'en', 'X-MeshPi-CSRF': 'wrong'})
    assert status == 403
    assert json.loads(raw)['error'] == 'Reload the page before trying again.'
    assert calls == []


def test_auth_errors_are_translated_in_the_active_request_language():
    with using_language('nb'), pytest.raises(AuthError) as error:
        canonical_origin('https://example.com/path')
    assert str(error.value) == 'Innloggingsadressen må være et opphav uten sti.'
    with using_language('en'), pytest.raises(AuthError) as error:
        canonical_origin('https://example.com/path')
    assert str(error.value) == 'The sign-in address must be an origin without a path.'


def test_older_daemon_reasons_translate_without_changing_messages_names_or_machine_codes():
    result = {'ok': True, 'data': {
        'node_id': '!1234abcd', 'action': 'traceroute', 'status': 'failed',
        'reason': 'Meshtastic-noden er ikkje tilkopla',
        'text': 'Meshtastic-noden er ikkje tilkopla',
        'long_name': 'Meshtastic-noden er ikkje tilkopla',
        'reports': [{'error': 'Målnoden svara ikkje (NO_RESPONSE)'}],
    }}
    with using_language('nb'):
        translated = _daemon_result(result)
        assert _daemon_text('Unknown external error') == 'Unknown external error'
    assert translated['data']['reason'] == 'Meshtastic-noden er ikke tilkoblet'
    assert translated['data']['reports'][0]['error'] == 'Målnoden svarte ikke (NO_RESPONSE)'
    assert translated['data']['text'] == result['data']['text']
    assert translated['data']['long_name'] == result['data']['long_name']
    assert translated['data']['node_id'] == '!1234abcd'
    assert translated['data']['status'] == 'failed'
    assert result['data']['reason'] == 'Meshtastic-noden er ikkje tilkopla'
