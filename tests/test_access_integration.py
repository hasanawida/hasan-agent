"""Real server auth/screen route regressions with fake capture and no native input."""
import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from hassan_ai import pc_tools, remote, server
from hassan_ai.browser_sessions import COOKIE
from .conftest import make_settings

LOCAL = {'origin': 'http://testserver'}
PHONE = {'host': 'pc.ts.net', 'origin': 'https://pc.ts.net', 'x-forwarded-for': '100.64.0.4', 'x-forwarded-proto': 'https'}


def phone(token):
    return {**PHONE, 'cookie': COOKIE + '=' + token}


def login(client, key=None):
    response = client.get('/login', params={'key': key or client.app.state.access_key}, headers=PHONE, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.cookies.get(COOKIE), response


def test_login_issues_per_browser_http_only_cookie_without_master_credential(client):
    token, response = login(client)
    assert token and token != client.app.state.access_key
    cookie = next(value for value in response.headers.get_list('set-cookie') if value.startswith(COOKIE + '='))
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Secure' in cookie
    assert client.app.state.access_key not in ' '.join(response.headers.values())
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert client.get('/api/health', headers=phone(token)).status_code == 200
    metadata = client.get('/api/devices', headers=phone(token)).json()
    assert metadata['current_id'] and len(metadata['devices']) == 1
    assert token not in str(metadata) and 'token_hash' not in str(metadata)


def test_each_login_is_unique_and_revocation_affects_only_one_browser(client):
    first, _ = login(client)
    second, _ = login(client)
    assert first != second
    ident = client.get('/api/devices', headers=phone(first)).json()['current_id']
    assert client.post(f'/api/devices/{ident}/revoke', headers=LOCAL).status_code == 200
    assert client.get('/api/health', headers=phone(first)).status_code == 401
    assert client.get('/api/health', headers=phone(second)).status_code == 200
    assert client.get('/api/health').status_code == 200  # Local recovery access remains.


def test_legacy_cookie_migrates_once_but_revoked_cookie_never_falls_back(client):
    key = client.app.state.access_key
    migrated = client.get('/api/devices', headers={**PHONE, 'cookie': f'hassan_key={key}'})
    assert migrated.status_code == 200
    token = migrated.cookies.get(COOKIE)
    assert token and token != key
    ident = migrated.json()['current_id']
    assert ident and migrated.json()['devices'][0]['legacy_migrated'] is True
    assert any('hassan_key=' in cookie and 'Max-Age=0' in cookie for cookie in migrated.headers.get_list('set-cookie'))
    before = len(client.app.state.browser_sessions.list_public())
    assert client.get('/api/devices', headers=phone(token)).status_code == 200
    assert len(client.app.state.browser_sessions.list_public()) == before
    assert client.post(f'/api/devices/{ident}/revoke', headers=LOCAL).status_code == 200
    fallback = {**PHONE, 'cookie': f'{COOKIE}={token}; hassan_key={key}', 'authorization': f'Bearer {key}'}
    assert client.get('/api/health', headers=fallback).status_code == 401
    assert len(client.app.state.browser_sessions.list_public()) == 0


@pytest.mark.parametrize('origin', ['https://pc.ts.net:444', 'http://pc.ts.net', 'https://evil.test', 'https://pc.ts.net/'])
def test_mutations_reject_different_ports_schemes_hosts_or_origin_paths(client, origin):
    token, _ = login(client)
    assert client.post('/api/devices/rename', headers={**phone(token), 'origin': origin}, json={'label': 'Phone'}).status_code == 403


def test_numeric_pairing_code_is_consumed_by_real_login_route(client):
    code = client.post('/api/remote/pair-code', headers=LOCAL).json()['code']
    token, _ = login(client, code)
    assert client.get('/api/health', headers=phone(token)).status_code == 200
    assert client.get('/login', params={'key': code}, headers=PHONE, follow_redirects=False).status_code == 401


def test_local_qr_url_uses_short_lived_one_use_ticket_instead_of_master(tmp_path):
    settings = make_settings(tmp_path)
    settings.public_url = 'https://pc.ts.net'
    with TestClient(server.create_app(settings)) as client:
        info = client.get('/api/remote').json()
        assert client.app.state.access_key not in str(info)
        item = info['urls'][0]
        assert 0 < item['expires_in'] <= 300
        key = parse_qs(urlsplit(item['pair_url']).query)['key'][0]
        assert key.startswith('qr_')
        token, response = login(client, key)
        assert client.get('/api/health', headers=phone(token)).status_code == 200
        assert response.headers['referrer-policy'] == 'no-referrer'
        assert client.get('/login', params={'key': key}, headers=PHONE, follow_redirects=False).status_code == 401


def test_expired_qr_ticket_is_rejected_without_creating_browser_session(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.public_url = 'https://pc.ts.net'
    monkeypatch.setattr(remote.PairingCodes, 'TICKET_TTL', 0)
    with TestClient(server.create_app(settings)) as client:
        info = client.get('/api/remote').json()
        key = parse_qs(urlsplit(info['urls'][0]['pair_url']).query)['key'][0]
        response = client.get('/login', params={'key': key}, headers=PHONE, follow_redirects=False)
        assert response.status_code == 401
        assert not client.app.state.browser_sessions.list_public()


@pytest.fixture
def fake_capture(monkeypatch):
    monitors = [
        {'id': 'desktop', 'label': 'All', 'left': -800, 'top': 0, 'width': 2720, 'height': 1080, 'primary': False},
        {'id': 'left', 'label': 'Second', 'left': -800, 'top': 0, 'width': 800, 'height': 600, 'primary': False},
    ]
    monkeypatch.setattr(pc_tools, 'list_monitors', lambda: [dict(value) for value in monitors])
    monkeypatch.setattr(server, 'list_monitors', lambda: [dict(value) for value in monitors])
    calls, processes = [], []
    async def argv(self, profile='balanced', monitor='desktop'):
        # Reuse real request validation; no ffmpeg or capture APIs are used.
        resolved = pc_tools.resolve_screen_capture(profile, monitor)
        return ['fake-screen', profile, monitor, str(resolved['monitor']['width'])]
    monkeypatch.setattr(pc_tools.PCTools, 'live_screen_argv', argv)
    class Proc:
        def __init__(self):
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(b'fake-multipart-frame')
            self.stdout.feed_eof()
            self.killed = False
        def kill(self):
            self.killed = True
            self.returncode = -1
        async def wait(self):
            return self.returncode
    async def create(*args, **kwargs):
        calls.append(args)
        proc = Proc(); processes.append(proc)
        return proc
    monkeypatch.setattr(server.asyncio, 'create_subprocess_exec', create)
    return monitors, calls, processes


def test_screen_options_start_and_stream_resolve_selected_profile_monitor(client, fake_capture):
    monitors, calls, processes = fake_capture
    options = client.get('/api/screen/options').json()
    assert {profile['id'] for profile in options['profiles']} == {'economy', 'balanced', 'sharp'}
    assert options['monitors'] == monitors
    response = client.post('/api/live/screen', headers=LOCAL, json={'profile': 'sharp', 'monitor': 'left'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['capture'] == monitors[1] and body['virtual'] == monitors[0]
    assert body['profile'] == 'sharp'
    assert parse_qs(urlsplit(body['url']).query)['token'][0] == body['stream_id']
    assert calls == []  # A start request only reserves; opening the image starts capture.
    streamed = client.get(body['url'])
    assert streamed.status_code == 200 and streamed.content == b'fake-multipart-frame'
    assert calls == [('fake-screen', 'sharp', 'left', '800')]
    assert processes[0].killed
    assert client.get(body['url']).status_code == 403  # No stream-token replay.


def test_screen_reservation_rejects_competitors_and_wrong_or_missing_stop_tokens(client, fake_capture):
    first = client.post('/api/live/screen', headers=LOCAL).json()
    assert client.post('/api/live/screen', headers=LOCAL, json={'profile': 'economy'}).status_code == 409
    assert client.post('/api/live/screen/stop', headers=LOCAL).status_code == 403
    assert client.post('/api/live/screen/stop', headers=LOCAL, json={'token': 'wrong'}).status_code == 403
    assert client.post('/api/live/screen/stop', headers=LOCAL, json={'token': first['stream_id']}).status_code == 200
    assert client.get(first['url']).status_code == 403
    second = client.post('/api/live/screen', headers=LOCAL, json={'profile': 'economy'}).json()
    assert second['stream_id'] != first['stream_id']
    assert client.post('/api/live/screen/stop', headers=LOCAL, json={'token': first['stream_id']}).status_code == 403
    assert client.get(second['url']).status_code == 200


@pytest.mark.parametrize('payload', [{'profile': 'unbounded'}, {'monitor': 'not-connected'}, {'fps': 1000}, {'profile': 50}])
def test_invalid_capture_options_never_create_a_capture_process(client, fake_capture, payload):
    assert client.post('/api/live/screen', headers=LOCAL, json=payload).status_code == 422
    assert fake_capture[1] == []


def test_removed_monitor_is_rejected_before_capture_starts(client, fake_capture):
    monitors, calls, _ = fake_capture
    first = client.post('/api/live/screen', headers=LOCAL, json={'monitor': 'left'}).json()
    monitors.pop()
    assert client.get(first['url']).status_code == 503
    assert calls == []


def test_changed_monitor_geometry_cannot_use_stale_click_mapping(client, fake_capture):
    monitors, calls, _ = fake_capture
    first = client.post('/api/live/screen', headers=LOCAL, json={'monitor': 'left'}).json()
    monitors[1]['left'] = 0
    monitors[1]['width'] = 1000
    # The HTML client already received the old rectangle for normalized mouse mapping.
    # Refuse capture rather than show a different rectangle with that stale mapping.
    assert client.get(first['url']).status_code in (409, 422, 503)
    assert calls == []
