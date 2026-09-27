"""File transfer limits and explicit clipboard authorization; native access is always fake."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import URL

from hassan_ai import transfers
from hassan_ai.transfers import attach_transfer_routes, safe_filename

LOCAL = {'origin': 'http://testserver'}
REMOTE = {'host': 'pc.ts.net', 'origin': 'https://pc.ts.net', 'x-forwarded-for': '100.64.0.8', 'x-forwarded-proto': 'https'}


def owner(*, local=True, session=None, key=None):
    headers = {'host': 'testserver' if local else 'pc.ts.net'}
    if not local:
        headers.update(REMOTE)
    cookies = {'hassan_session': session} if session else {'hassan_key': key} if key else {}
    return SimpleNamespace(headers=headers, cookies=cookies, client=SimpleNamespace(host='testclient'),
                           url=URL('ws://testserver/api/desktop/control'))


@pytest.fixture
def transfer_app(tmp_path, monkeypatch):
    native = []
    def fake_native(action, text=''):
        native.append((action, text))
        return 'نص عربي من الكمبيوتر' if action == 'read' else ''
    monkeypatch.setattr(transfers, 'native_clipboard', fake_native)
    desktop = SimpleNamespace(lock=asyncio.Lock(), enabled=False, owner=None)
    desktop.permitted = lambda: desktop.enabled
    async def work(fn, *args):
        return fn(*args)
    desktop.work = work
    app = FastAPI()
    app.state.access_key = 'test-key'
    def authenticate(request):
        session = request.cookies.get('hassan_session')
        return session in ('owner-token', 'other-token') if session else transfers.remote.key_ok(transfers.remote.presented_key(request), 'test-key')
    app.state.authenticate_browser = authenticate
    settings = SimpleNamespace(data_dir=tmp_path / 'data', allowed_hosts=['testserver'], trusted_clients=['testclient'])
    attach_transfer_routes(app, settings, desktop)
    return app, desktop, native


@pytest.fixture
def client(transfer_app):
    with TestClient(transfer_app[0]) as client:
        yield client


def test_upload_download_are_opaque_attachments_with_integrity(client, transfer_app):
    data = 'ملف من الهاتف'.encode()
    response = client.post('/api/transfers', params={'name': '../../CON.txt'}, content=data, headers=LOCAL)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row['name'] == '_CON.txt' and row['sha256'] == hashlib.sha256(data).hexdigest()
    assert len(row['id']) == 32
    fetched = client.get('/api/transfers/' + row['id'])
    assert fetched.content == data
    assert fetched.headers['content-type'] == 'application/octet-stream'
    assert fetched.headers['content-disposition'].startswith('attachment;')
    assert fetched.headers['cache-control'] == 'no-store'
    assert fetched.headers['x-content-type-options'] == 'nosniff'
    assert fetched.headers['x-content-sha256'] == row['sha256']
    assert client.get('/api/transfers').json()['files'] == [row]
    assert client.get('/api/transfers/not-an-id').status_code == 404
    assert transfer_app[2] == []


@pytest.mark.parametrize('name', ['..', 'C:\\tmp\\file.txt', '<bad>:name?.txt', 'A\u202eexe.txt', 'LPT1', 'file. '])
def test_safe_name_never_keeps_paths_controls_or_reserved_names(name):
    sanitized = safe_filename(name)
    assert sanitized == safe_filename(sanitized)
    assert '/' not in sanitized and '\\' not in sanitized and ':' not in sanitized
    assert sanitized not in ('.', '..', 'LPT1')
    assert '\u202e' not in sanitized


def test_remote_auth_and_exact_origin_are_separate_requirements(client):
    assert client.get('/api/transfers', headers=REMOTE).status_code == 401
    assert client.post('/api/transfers', content=b'a').status_code == 403
    assert client.post('/api/transfers', content=b'a', headers={'origin': 'http://testserver:9999'}).status_code == 403
    auth = {**REMOTE, 'cookie': 'hassan_session=owner-token'}
    assert client.post('/api/transfers?name=phone.txt', content=b'ok', headers=auth).status_code == 201
    assert client.get('/api/transfers', headers={**REMOTE, 'cookie': 'hassan_session=invalid', 'authorization': 'Bearer test-key'}).status_code == 401


def test_upload_limits_clean_up_partial_files(client, transfer_app, monkeypatch):
    monkeypatch.setattr(transfers, 'MAX_FILE', 8)
    root = transfer_app[0].state.transfers.root
    response = client.post('/api/transfers', content=b'123456789', headers=LOCAL)
    assert response.status_code == 413
    assert not root.exists() or not list(root.glob('*.part'))
    # Chunked uploads are bounded independently of Content-Length.
    response = client.post('/api/transfers', content=iter([b'1234', b'56789']), headers=LOCAL)
    assert response.status_code == 413
    assert not list(root.glob('*.part')) and not list(root.glob('*.bin'))


def test_total_and_file_count_quotas_include_previous_uploads(client, monkeypatch):
    monkeypatch.setattr(transfers, 'MAX_FILE', 8)
    monkeypatch.setattr(transfers, 'MAX_TOTAL', 12)
    assert client.post('/api/transfers', content=b'1234567', headers=LOCAL).status_code == 201
    assert client.post('/api/transfers', content=b'123456', headers=LOCAL).status_code == 413
    monkeypatch.setattr(transfers, 'MAX_FILES', 1)
    assert client.post('/api/transfers', content=b'1', headers=LOCAL).status_code == 413


def test_malformed_registry_fails_closed_and_is_not_overwritten(client, transfer_app):
    assert client.get('/api/transfers').status_code == 200
    store = transfer_app[0].state.transfers
    store.index.write_text('{broken', encoding='utf-8')
    assert client.get('/api/transfers').status_code == 503
    assert client.post('/api/transfers', content=b'x', headers=LOCAL).status_code == 503
    assert store.index.read_text(encoding='utf-8') == '{broken'


def test_registered_file_tampering_is_not_served(client, transfer_app):
    row = client.post('/api/transfers', content=b'one', headers=LOCAL).json()
    (transfer_app[0].state.transfers.root / (row['id'] + '.bin')).write_bytes(b'changed size')
    assert client.get('/api/transfers/' + row['id']).status_code == 503


def test_symlink_storage_cannot_escape(client, transfer_app, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = transfer_app[0].state.transfers.root
    root.parent.mkdir()
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation is unavailable for this Windows account')
    assert client.post('/api/transfers', content=b'private', headers=LOCAL).status_code == 503
    assert not list(outside.iterdir())


def test_only_one_upload_can_hold_the_storage_budget(transfer_app):
    app = transfer_app[0]
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_body():
            entered.set()
            await release.wait()
            yield b'first'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as c:
            # This transport has a network client address; use a valid remote credential.
            headers = {**LOCAL, 'authorization': 'Bearer test-key'}
            first = asyncio.create_task(c.post('/api/transfers', content=slow_body(), headers=headers))
            await entered.wait()
            second = await c.post('/api/transfers', content=b'second', headers=headers)
            assert second.status_code == 409
            release.set()
            assert (await first).status_code == 201
    asyncio.run(scenario())


def test_clipboard_never_runs_on_listing_or_without_manual_grant(client, transfer_app):
    _, desktop, calls = transfer_app
    client.get('/api/transfers')
    assert calls == []
    assert client.post('/api/clipboard/read', headers=LOCAL).status_code == 403
    desktop.enabled = True
    assert client.post('/api/clipboard/read', headers=LOCAL).status_code == 403
    desktop.owner = owner()
    assert client.post('/api/clipboard/read').status_code == 403
    assert calls == []
    assert client.post('/api/clipboard/read', headers=LOCAL).json()['text'] == 'نص عربي من الكمبيوتر'
    assert calls == [('read', '')]
    desktop.enabled = False
    assert client.post('/api/clipboard/read', headers=LOCAL).status_code == 403
    assert len(calls) == 1


def test_remote_clipboard_requires_the_exact_controlling_device(client, transfer_app):
    _, desktop, calls = transfer_app
    desktop.enabled = True
    desktop.owner = owner(local=False, session='owner-token')
    mismatch = {**REMOTE, 'cookie': 'hassan_session=other-token'}
    assert client.post('/api/clipboard/read', headers=mismatch).status_code == 403
    assert client.post('/api/clipboard/read', headers=LOCAL).status_code == 403
    assert calls == []
    auth = {**REMOTE, 'cookie': 'hassan_session=owner-token'}
    assert client.post('/api/clipboard/write', headers=auth, json={'text': 'مرحبا'}).status_code == 200
    assert calls == [('write', 'مرحبا')]
    desktop.owner = owner(local=False, key='test-key')
    assert client.post('/api/clipboard/read', headers={**REMOTE, 'authorization': 'Bearer test-key'}).status_code == 200


def test_clipboard_rejects_size_type_nul_and_native_failures(client, transfer_app, monkeypatch):
    _, desktop, calls = transfer_app
    desktop.enabled = True; desktop.owner = owner()
    assert client.post('/api/clipboard/write', headers=LOCAL, json={'text': 'x' * 65537}).status_code == 422
    assert client.post('/api/clipboard/write', headers=LOCAL, json={'text': 42}).status_code == 422
    assert client.post('/api/clipboard/write', headers=LOCAL, json={'text': 'x\0y'}).status_code == 400
    assert calls == []
    def busy(*_args):
        raise RuntimeError('busy')
    monkeypatch.setattr(transfers, 'native_clipboard', busy)
    response = client.post('/api/clipboard/read', headers=LOCAL)
    assert response.status_code == 503
    assert 'text' not in response.json()


def test_same_size_file_changes_fail_integrity_check(client, transfer_app):
    row = client.post('/api/transfers', content=b'one', headers=LOCAL).json()
    (transfer_app[0].state.transfers.root / (row['id'] + '.bin')).write_bytes(b'two')
    assert client.get('/api/transfers/' + row['id']).status_code == 503


def test_cancelled_upload_removes_only_its_uncommitted_partial_file(transfer_app):
    app = transfer_app[0]
    async def scenario():
        started = asyncio.Event()
        async def broken_upload():
            yield b'partial'
            started.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as c:
            task = asyncio.create_task(c.post('/api/transfers', content=broken_upload(), headers={**LOCAL, 'authorization': 'Bearer test-key'}))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not list(app.state.transfers.root.glob('*.part'))
            assert not list(app.state.transfers.root.glob('*.bin'))
            assert (await c.get('/api/transfers', headers={'authorization': 'Bearer test-key'})).json()['files'] == []
    asyncio.run(scenario())
