"""Explicit browser file exchange and owner-bound Windows clipboard access."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictStr

from . import remote
from .desktop import same_origin
from .processes import finish_thread_call

MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 250 * 1024 * 1024
MAX_FILES = 200
MAX_CLIPBOARD = 65536
_IDENTIFIER = re.compile(r"^[0-9a-f]{32}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_NATIVE_LOCK = threading.Lock()


def safe_filename(value: str) -> str:
    value = unicodedata.normalize("NFC", value.replace("\\", "/").split("/")[-1])
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', value).strip().rstrip('. ')
    # Format controls can visually disguise executable names in download dialogs.
    value = ''.join(c for c in value if unicodedata.category(c) != 'Cf')[:180].rstrip('. ')
    if not value or value in ('.', '..'):
        value = 'file'
    if re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def _not_link(path: Path, *, regular=False):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
        raise OSError('Links are not allowed in transfer storage')
    if regular and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise OSError('Transfer entry is not a regular independent file')
    return info


class TransferStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir).absolute() / 'transfers'
        self.index = self.root / 'index.json'
        self.upload_lock = asyncio.Lock()

    def _directory(self):
        if self.root.parent.resolve() != self.root.parent:
            raise OSError('Transfer data path contains a link')
        self.root.mkdir(parents=True, exist_ok=True)
        _not_link(self.root)
        if self.root.resolve() != self.root:
            raise OSError('Transfer storage path contains a link')

    def _file(self, name: str):
        self._directory()
        path = self.root / name
        if path.parent != self.root or path.resolve().parent != self.root:
            raise OSError('Transfer path escaped storage')
        return path

    def load(self) -> list[dict]:
        try:
            self._directory()
            if self.index.is_symlink():
                raise OSError('Registry cannot be a link')
            if not self.index.exists():
                return []
            info = _not_link(self.index, regular=True)
            if info.st_size > 256 * 1024:
                raise ValueError('Registry too large')
            rows = json.loads(self.index.read_text(encoding='utf-8'))
            if not isinstance(rows, list) or len(rows) > MAX_FILES:
                raise ValueError('Invalid registry')
            ids = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {'id', 'name', 'size', 'sha256', 'created_at'}:
                    raise ValueError('Invalid entry')
                if not isinstance(row['id'], str) or not _IDENTIFIER.fullmatch(row['id']) or row['id'] in ids:
                    raise ValueError('Invalid id')
                if not isinstance(row['name'], str) or row['name'] != safe_filename(row['name']):
                    raise ValueError('Invalid name')
                if type(row['size']) is not int or not 0 <= row['size'] <= MAX_FILE:
                    raise ValueError('Invalid size')
                if not isinstance(row['sha256'], str) or not _HASH.fullmatch(row['sha256']):
                    raise ValueError('Invalid digest')
                if type(row['created_at']) not in (int, float) or not math.isfinite(row['created_at']) or row['created_at'] < 0:
                    raise ValueError('Invalid timestamp')
                info = _not_link(self._file(row['id'] + '.bin'), regular=True)
                if info.st_size != row['size']:
                    raise ValueError('Transfer file size mismatch')
                ids.add(row['id'])
            if sum(row['size'] for row in rows) > MAX_TOTAL:
                raise ValueError('Registry quota exceeded')
            return rows
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(503, 'تعذّر التحقق من مخزن الملفات. لم يتم فتح أي ملف.') from exc

    def disk_usage(self) -> tuple[int, int]:
        # Count orphaned uploads too: a crash must never bypass the disk quota.
        count = total = 0
        for path in self.root.iterdir():
            if path.name == 'index.json':
                continue
            info = _not_link(path, regular=True)
            if path.suffix not in ('.bin', '.part', '.new') or not _IDENTIFIER.fullmatch(path.stem):
                raise HTTPException(503, 'مخزن الملفات يحتوي على عنصر غير معروف.')
            count += 1
            total += info.st_size
        return count, total

    def save(self, rows):
        temporary = self._file(secrets.token_hex(16) + '.new')
        try:
            with temporary.open('x', encoding='utf-8') as output:
                json.dump(rows, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            if self.index.exists():
                _not_link(self.index, regular=True)
            temporary.replace(self.index)
        finally:
            temporary.unlink(missing_ok=True)

    def open_download(self, identifier):
        if not _IDENTIFIER.fullmatch(identifier):
            raise HTTPException(404, 'الملف غير موجود.')
        rows = self.load()
        row = next((item for item in rows if item['id'] == identifier), None)
        if row is None:
            raise HTTPException(404, 'الملف غير موجود.')
        try:
            path = self._file(identifier + '.bin')
            before = _not_link(path, regular=True)
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
            output = os.fdopen(fd, 'rb')
            after = os.fstat(output.fileno())
            if before.st_ino != after.st_ino or after.st_size != row['size'] or after.st_nlink != 1:
                output.close()
                raise OSError('File changed during download open')
            try:
                digest = hashlib.sha256()
                while chunk := output.read(64 * 1024):
                    digest.update(chunk)
                if not hmac.compare_digest(digest.hexdigest(), row['sha256']):
                    raise OSError('File integrity changed')
                output.seek(0)
                return row, output
            except BaseException:
                output.close()
                raise
        except OSError as exc:
            raise HTTPException(503, 'تعذّر التحقق من الملف المطلوب.') from exc


def _credential(connection) -> bytes:
    session = connection.cookies.get('hassan_session')
    if session:
        return ('session:' + session).encode('utf-8')
    value = remote.presented_key(connection)
    return ('legacy:' + value).encode('utf-8') if value else b''


def _require_access(request, settings):
    local = remote.is_local(request, settings.allowed_hosts, settings.trusted_clients)
    authenticate = getattr(request.app.state, 'authenticate_browser', None)
    authenticated = bool(authenticate(request)) if authenticate else remote.key_ok(remote.presented_key(request), request.app.state.access_key)
    if not local and not authenticated:
        raise HTTPException(401, 'سجّل الدخول أولًا.')
    return local


def _clipboard_allowed(request, settings, desktop):
    local = _require_access(request, settings)
    if not same_origin(request):
        raise HTTPException(403, 'هذا الطلب يحتاج نفس عنوان الصفحة.')
    if not desktop.permitted() or desktop.owner is None:
        raise HTTPException(403, 'ابدأ التحكّم اليدوي بالكمبيوتر أولًا لاستخدام النسخ واللصق.')
    owner = desktop.owner
    owner_local = remote.is_local(owner, settings.allowed_hosts, settings.trusted_clients)
    if local and owner_local:
        return
    candidate, current = _credential(request), _credential(owner)
    if not candidate or not current or not hmac.compare_digest(candidate, current):
        raise HTTPException(403, 'النسخ واللصق متاح فقط للجهاز الذي يتحكّم الآن.')


def native_clipboard(action: str, text: str = '') -> str:
    """Called explicitly on the desktop worker; never read automatically."""
    if os.name != 'nt':
        raise RuntimeError('الحافظة متاحة على ويندوز فقط.')
    import ctypes
    from ctypes import wintypes
    with _NATIVE_LOCK:
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        user.OpenClipboard.argtypes = [wintypes.HWND]; user.OpenClipboard.restype = wintypes.BOOL
        user.GetClipboardData.argtypes = [wintypes.UINT]; user.GetClipboardData.restype = wintypes.HANDLE
        user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]; user.SetClipboardData.restype = wintypes.HANDLE
        user.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]; user.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        user.CreateWindowExW.restype = wintypes.HWND
        user.DestroyWindow.argtypes = [wintypes.HWND]
        kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]; kernel.GlobalSize.restype = ctypes.c_size_t
        kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]; kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]; kernel.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]; kernel.GlobalFree.restype = wintypes.HGLOBAL
        window = None
        opened = False
        try:
            if action == 'write':
                window = user.CreateWindowExW(0, 'STATIC', 'Hassan clipboard', 0, 0, 0, 0, 0, wintypes.HWND(-3), None, None, None)
                if not window:
                    raise RuntimeError('تعذّر تهيئة الحافظة.')
            for _ in range(5):
                if user.OpenClipboard(window):
                    opened = True
                    break
                time.sleep(.02)
            if not opened:
                raise RuntimeError('الحافظة مشغولة. جرّب بعد قليل.')
            if action == 'read':
                if not user.IsClipboardFormatAvailable(13):
                    return ''
                handle = user.GetClipboardData(13)
                size = kernel.GlobalSize(handle) if handle else 0
                if not size or size > (MAX_CLIPBOARD + 1) * 4 or size % 2:
                    raise RuntimeError('نص الحافظة غير صالح أو أكبر من الحد المسموح.')
                pointer = kernel.GlobalLock(handle)
                if not pointer:
                    raise RuntimeError('تعذّر قراءة الحافظة.')
                try:
                    value = ctypes.string_at(pointer, size).decode('utf-16-le').split('\x00', 1)[0]
                    if len(value) > MAX_CLIPBOARD:
                        raise RuntimeError('نص الحافظة أكبر من الحد المسموح.')
                    return value
                finally:
                    kernel.GlobalUnlock(handle)
            if action != 'write' or len(text) > MAX_CLIPBOARD or '\x00' in text:
                raise RuntimeError('نص الحافظة غير صالح.')
            payload = (text + '\x00').encode('utf-16-le')
            handle = kernel.GlobalAlloc(0x0002, len(payload))
            if not handle:
                raise RuntimeError('تعذّر تخصيص ذاكرة للحافظة.')
            owned = True
            try:
                pointer = kernel.GlobalLock(handle)
                if not pointer:
                    raise RuntimeError('تعذّر تجهيز نص الحافظة.')
                try:
                    ctypes.memmove(pointer, payload, len(payload))
                finally:
                    kernel.GlobalUnlock(handle)
                if not user.EmptyClipboard() or not user.SetClipboardData(13, handle):
                    raise RuntimeError('تعذّر تحديث الحافظة.')
                owned = False
                return ''
            finally:
                if owned:
                    kernel.GlobalFree(handle)
        finally:
            if opened:
                user.CloseClipboard()
            if window:
                user.DestroyWindow(window)


async def _clipboard_work(desktop, *args):
    # Request cancellation must not release ownership while native work still runs.
    work = asyncio.create_task(desktop.work(native_clipboard, *args))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        try:
            await work
        finally:
            raise


class ClipboardText(BaseModel):
    text: StrictStr = Field(max_length=MAX_CLIPBOARD)


def attach_transfer_routes(app, settings, desktop):
    store = TransferStore(settings.data_dir)
    app.state.transfers = store

    @app.get('/api/transfers')
    async def files(request: Request):
        _require_access(request, settings)
        rows = await finish_thread_call(store.load)
        return JSONResponse({'files': sorted(rows, key=lambda row: row['created_at'], reverse=True),
                             'limits': {'file_bytes': MAX_FILE, 'total_bytes': MAX_TOTAL, 'files': MAX_FILES}}, headers=_HEADERS)

    @app.post('/api/transfers', status_code=201)
    async def upload(request: Request, name: str = 'file'):
        _require_access(request, settings)
        if not same_origin(request):
            raise HTTPException(403, 'هذا الطلب يحتاج نفس عنوان الصفحة.')
        if len(name) > 1024:
            raise HTTPException(400, 'اسم الملف طويل جدًا.')
        if store.upload_lock.locked():
            raise HTTPException(409, 'هناك ملف قيد الرفع. انتظر حتى يكتمل.')
        length = request.headers.get('content-length')
        if length:
            try:
                size = int(length)
                if size < 0 or size > MAX_FILE:
                    raise HTTPException(413, 'حجم الملف يجب ألا يتجاوز 25 ميغابايت.')
            except ValueError:
                raise HTTPException(400, 'حجم الملف غير صالح.')
        async with store.upload_lock:
            rows = await finish_thread_call(store.load)
            try:
                count, used = await finish_thread_call(store.disk_usage)
            except OSError as exc:
                raise HTTPException(503, 'تعذّر التحقق من مساحة مخزن الملفات.') from exc
            if count >= MAX_FILES or used >= MAX_TOTAL:
                raise HTTPException(413, 'مخزن النقل ممتلئ.')
            identifier = secrets.token_hex(16)
            temporary = store._file(identifier + '.part')
            destination = store._file(identifier + '.bin')
            written = 0
            digest = hashlib.sha256()
            complete = False
            try:
                with temporary.open('xb') as output:
                    iterator = request.stream().__aiter__()
                    deadline = time.monotonic() + 300
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise HTTPException(408, 'استغرق رفع الملف وقتًا طويلًا.')
                        try:
                            chunk = await asyncio.wait_for(iterator.__anext__(), min(30, remaining))
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            raise HTTPException(408, 'انقطع رفع الملف أو أصبح الاتصال بطيئًا جدًا.')
                        written += len(chunk)
                        if written > MAX_FILE or used + written > MAX_TOTAL:
                            raise HTTPException(413, 'الملف أو مساحة مخزن النقل تجاوزت الحد المسموح.')
                        digest.update(chunk)
                        await finish_thread_call(output.write, chunk)
                    await finish_thread_call(output.flush)
                    await finish_thread_call(os.fsync, output.fileno())
                temporary.replace(destination)
                entry = {'id': identifier, 'name': safe_filename(name), 'size': written,
                         'sha256': digest.hexdigest(), 'created_at': time.time()}
                await finish_thread_call(store.save, [*rows, entry])
                complete = True
                return JSONResponse(entry, status_code=201, headers=_HEADERS)
            finally:
                temporary.unlink(missing_ok=True)
                if not complete:
                    # If cancellation arrived during atomic registry save, retain committed data.
                    try:
                        committed = any(row['id'] == identifier for row in store.load())
                    except HTTPException:
                        committed = False
                    if not committed:
                        destination.unlink(missing_ok=True)

    @app.get('/api/transfers/{identifier}')
    async def download(identifier: str, request: Request):
        _require_access(request, settings)
        row, source = await finish_thread_call(store.open_download, identifier)
        async def chunks():
            try:
                while chunk := await finish_thread_call(source.read, 64 * 1024):
                    yield chunk
            finally:
                source.close()
        headers = {**_HEADERS, 'Content-Length': str(row['size']),
                   'Content-Disposition': "attachment; filename=\"download\"; filename*=UTF-8''" + quote(row['name'], safe=''),
                   'X-Content-SHA256': row['sha256']}
        return StreamingResponse(chunks(), media_type='application/octet-stream', headers=headers)

    @app.post('/api/clipboard/read')
    async def clipboard_read(request: Request):
        async with desktop.lock:
            _clipboard_allowed(request, settings, desktop)
            try:
                value = await _clipboard_work(desktop, 'read')
            except (OSError, RuntimeError, UnicodeError) as exc:
                raise HTTPException(503, 'تعذّر قراءة الحافظة. قد تكون مشغولة أو تحتوي نصًا أكبر من الحد المسموح.') from exc
            return JSONResponse({'text': value}, headers=_HEADERS)

    @app.post('/api/clipboard/write')
    async def clipboard_write(body: ClipboardText, request: Request):
        if '\x00' in body.text:
            raise HTTPException(400, 'النص لا يقبل محارف فارغة داخلية.')
        async with desktop.lock:
            _clipboard_allowed(request, settings, desktop)
            try:
                await _clipboard_work(desktop, 'write', body.text)
            except (OSError, RuntimeError, UnicodeError) as exc:
                raise HTTPException(503, 'تعذّر تحديث حافظة الكمبيوتر. جرّب بعد قليل.') from exc
        return JSONResponse({'ok': True}, headers=_HEADERS)
