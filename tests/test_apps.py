"""Internet, screen/camera/mic, Blender, ffmpeg, Windows-MCP policy and 'trust similar'."""

import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

import httpx
import pytest

from hassan_ai.config import POLICIES_DIR
from hassan_ai.pc_tools import PCTools
from hassan_ai.policy import APPROVAL, AUTO, FORBIDDEN, Policy, PolicyError

from .test_operator import op, pending, start
from .conftest import wait


def tools(tmp_path, transport=None) -> PCTools:
    return PCTools(Policy.load(POLICIES_DIR / "default.yaml"), [tmp_path.resolve()], tmp_path / "data" / "trash",
                   [tmp_path / "data"], media_dir=tmp_path / "data" / "media", http_transport=transport)


def test_web_fetch_blocks_local_and_private_addresses(tmp_path):
    t = tools(tmp_path)
    for url in ("http://127.0.0.1:8787/api/keys", "http://localhost/", "http://192.168.1.1/", "http://[::1]/",
                "file:///etc/passwd"):
        with pytest.raises(PolicyError):
            asyncio.run(t.execute("web_fetch", {"url": url}))


def test_web_fetch_and_search_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(PCTools, "_public_host", staticmethod(lambda host: None))

    def handler(request: httpx.Request):
        if request.url.host == "html.duckduckgo.com":
            body = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fblender.org%2F">'
                    'Blender <b>Home</b></a><a class="result__snippet" href="x">Free 3D &amp; open</a>')
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"})
        return httpx.Response(200, text="<html><script>x()</script><h1>Hello</h1><p>World &amp; more</p></html>",
                              headers={"content-type": "text/html; charset=utf-8"})

    t = tools(tmp_path, httpx.MockTransport(handler))
    page = asyncio.run(t.execute("web_fetch", {"url": "https://example.com/old"}))
    assert "Hello" in page and "World & more" in page and "x()" not in page and "/new" in page
    results = json.loads(asyncio.run(t.execute("web_search", {"query": "blender"})))
    assert results[0] == {"title": "Blender Home", "url": "https://blender.org/", "snippet": "Free 3D & open"}
    # a long query string could carry stolen data out, so it needs approval
    assert t.access("web_fetch", {"url": "https://x.com/?d=" + "A" * 400}) == APPROVAL
    assert t.access("web_fetch", {"url": "https://x.com/?q=blender"}) == AUTO


def test_camera_mic_need_approval_and_explain_missing_ffmpeg(tmp_path, monkeypatch):
    t = tools(tmp_path)
    assert t.access("camera_photo", {}) == APPROVAL
    assert t.access("mic_record", {"seconds": 5}) == APPROVAL
    assert t.access("screenshot", {}) == AUTO
    monkeypatch.setattr("hassan_ai.pc_tools.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="winget install Gyan.FFmpeg"):
        asyncio.run(t.execute("camera_photo", {}))


def test_blender_runs_script_headless_and_returns_render(tmp_path, monkeypatch):
    from .test_providers import make_cli
    folder = tmp_path / "bin"
    folder.mkdir()
    fake = make_cli(folder, "blender", r"""
        import sys, re, ast
        args = sys.argv[1:]
        assert args[0] == "-b"
        script = open(args[args.index("--python") + 1], encoding="utf-8").read()
        out = ast.literal_eval(re.search(r"^OUTPUT = (.+)$", script, re.M).group(1))
        print("blend:", args[1] if args[1].endswith(".blend") else "none")
        if "render.render" in script:
            open(out, "wb").write(b"PNG")
        print("script ran:", "cube" in script)
    """)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    (tmp_path / "scene.blend").write_text("x")
    t = tools(tmp_path)
    assert t.access("blender", {"script": "x"}) == APPROVAL
    res = json.loads(asyncio.run(t.execute("blender", {"script": "bpy.ops.mesh.primitive_cube_add()  # cube",
                                                         "blend_file": str(tmp_path / "scene.blend"), "render": True})))
    assert res["exit"] == 0 and "script ran: True" in res["output"] and "scene.blend" in res["output"]
    assert res["media"].startswith("render-") and (tmp_path / "data" / "media" / res["media"]).exists()


def test_windows_mcp_policy():
    p = Policy.load(POLICIES_DIR / "default.yaml")
    # tool names from windows-mcp 0.8.x
    for look in ("Snapshot", "Screenshot", "Wait", "WaitFor", "DisplayInventory"):
        assert p.decide_mcp("windows", look) == AUTO
    for act in ("Click", "Type", "App", "Shortcut", "Clipboard", "Process", "Scrape", "MultiEdit"):
        assert p.decide_mcp("windows", act) == APPROVAL
    for bypass in ("Registry", "PowerShell", "FileSystem"):
        assert p.decide_mcp("windows", bypass) == FORBIDDEN


def test_trust_similar_skips_repeat_approvals(client, tmp_path):
    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.txt").write_text(n)
    t = start(client, op("copy", src=str(tmp_path / "a.txt"), dst=str(tmp_path / "out" / "a.txt")),
              op("copy", src=str(tmp_path / "b.txt"), dst=str(tmp_path / "out" / "b.txt")),
              op("copy", src=str(tmp_path / "c.txt"), dst=str(tmp_path / "out" / "c.txt")))
    _, approval = pending(client, t["id"])
    assert approval["payload"]["trustable"] is True
    client.post(f"/api/approvals/{approval['id']}", json={"approve": True, "trust_similar": True})
    task = wait(client, t["id"], {"completed", "failed"})
    assert len(task["approvals"]) == 1  # the next two copies did not ask again
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["a.txt", "b.txt", "c.txt"]


def test_run_and_delete_are_never_trusted(client, tmp_path):
    for n in ("a", "b"):
        (tmp_path / f"{n}.log").write_text(n)
    t = start(client, op("delete", path=str(tmp_path / "a.log")), op("delete", path=str(tmp_path / "b.log")))
    _, first = pending(client, t["id"])
    assert first["payload"]["trustable"] is False
    client.post(f"/api/approvals/{first['id']}", json={"approve": True, "trust_similar": True})
    for _ in range(100):
        task = client.get(f"/api/tasks/{t['id']}").json()
        if len(task["approvals"]) == 2:
            break
        import time; time.sleep(0.05)
    assert len(task["approvals"]) == 2  # still asked for the second delete
    second = next(a for a in task["approvals"] if a["status"] == "pending")
    client.post(f"/api/approvals/{second['id']}", json={"approve": False})
    wait(client, t["id"], {"completed", "failed"})
    assert (tmp_path / "b.log").exists()


def test_media_endpoint_serves_only_media_files(client, tmp_path):
    media = tmp_path / "data" / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "screen-1.png").write_bytes(b"PNG")
    assert client.get("/api/media/screen-1.png").content == b"PNG"
    assert client.get("/api/media/..%2Faccess_key").status_code in (400, 404)
    assert client.get("/api/media/missing.png").status_code == 404
    phone = {"host": "pc.ts.net", "x-forwarded-for": "100.64.0.9"}
    assert client.get("/api/media/screen-1.png", headers=phone).status_code == 401


def test_windows_app_switch_is_automatic_and_titles_are_readable(tmp_path):
    t = tools(tmp_path)
    sw = {"server": "windows", "tool": "App", "arguments": {"mode": "switch", "name": "Microsoft Edge"}}
    launch = {"server": "windows", "tool": "App", "arguments": {"mode": "launch", "name": "CapCut"}}
    assert t.access("mcp", sw) == AUTO
    assert t.access("mcp", launch) == APPROVAL
    assert t.describe("mcp", sw) == "انتقل لبرنامج: Microsoft Edge"
    assert t.describe("mcp", launch) == "افتح برنامج: CapCut"
    click = {"server": "windows", "tool": "Click", "arguments": {"label": 12}}
    assert t.describe("mcp", click) == "انقر على العنصر رقم 12"
    typ = {"server": "windows", "tool": "Type", "arguments": {"label": 3, "text": "@BotFather", "press_enter": True}}
    assert t.describe("mcp", typ) == "اكتب «@BotFather» في العنصر رقم 3 ثم Enter"


def test_vscode_tool_opens_without_approval(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr("hassan_ai.pc_tools.subprocess.Popen", lambda argv, **kw: launched.append(argv))
    monkeypatch.setattr("hassan_ai.pc_tools.shutil.which", lambda name: "/usr/bin/code" if name == "code" else None)
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "main.py").write_text("x")
    t = tools(tmp_path)
    assert t.access("vscode", {"path": str(tmp_path / "proj")}) == AUTO
    asyncio.run(t.execute("vscode", {"path": str(tmp_path / "proj" / "main.py"), "line": 3}))
    assert launched[-1] == ["/usr/bin/code", "-g", f"{(tmp_path / 'proj' / 'main.py').resolve()}:3"]
    with pytest.raises(PolicyError):
        asyncio.run(t.execute("vscode", {"path": "/etc"}))


def test_open_app_by_name(tmp_path, monkeypatch):
    t = tools(tmp_path)
    t._apps_cache = (__import__("time").time(), [
        {"name": "CapCut", "id": "C:\\Users\\h\\AppData\\Local\\CapCut\\Apps\\CapCut.exe"},
        {"name": "CapCut Uninstall", "id": "uninst"}, {"name": "Microsoft Word", "id": "word"}])
    launched = []
    monkeypatch.setattr(PCTools, "_launch_app", staticmethod(launched.append))
    assert t.access("open_app", {"name": "capcut"}) == APPROVAL  # starting a program waits for Hassan
    assert t.access("find_apps", {"query": "cap"}) == AUTO
    assert asyncio.run(t.execute("find_apps", {"query": "cap cut"})) .startswith('[\n "CapCut"')
    assert "started CapCut" in asyncio.run(t.execute("open_app", {"name": "Cap Cut"}))
    assert "started Microsoft Word" in asyncio.run(t.execute("open_app", {"name": "word"}))
    assert launched == ["C:\\Users\\h\\AppData\\Local\\CapCut\\Apps\\CapCut.exe", "word"]
    with pytest.raises(RuntimeError, match="Did you mean: CapCut"):
        asyncio.run(t.execute("open_app", {"name": "CapCat"}))


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_make_video_with_arabic_slides(tmp_path):
    t = tools(tmp_path)
    assert t.access("make_video", {"slides": []}) == AUTO
    res = json.loads(asyncio.run(t.execute("make_video", {
        "slides": [{"text": "تربية الأطفال", "seconds": 2}, {"text": "اسمع لطفلك", "seconds": 2, "bg": "#224466"}],
        "size": "square", "name": "kids"})))
    out = Path(res["saved"])
    assert out.suffix == ".mp4" and out.stat().st_size > 1000 and res["seconds"] == 4.0
    assert not any(p.name.startswith(".") for p in out.parent.iterdir())  # temp slides cleaned up
    with pytest.raises(PolicyError):  # pictures must come from the allowed folders
        asyncio.run(t.execute("make_video", {"slides": [{"text": "x", "image": "/etc/passwd"}]}))


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_live_camera_stream_needs_a_fresh_token(client):
    tools = client.app.state.orchestrator.operator.tools

    async def fake_argv(device=""):
        return [tools._ffmpeg(), "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i",
                "testsrc=size=320x240:rate=10:duration=1", "-f", "mpjpeg", "pipe:1"]

    tools.live_camera_argv = fake_argv
    assert client.get("/api/live/camera?token=guess").status_code == 403
    url = client.post("/api/live/camera").json()["url"]
    with client.stream("GET", url) as resp:
        assert resp.headers["content-type"].startswith("multipart/x-mixed-replace")
        first = next(resp.iter_bytes())
        assert b"--ffmpeg" in first and b"image/jpeg" in first
    assert client.get(url).status_code == 403  # a token works once
    client.post("/api/live/camera/stop")


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_intercom_hear_the_pc_and_talk_to_it(client, tmp_path, monkeypatch):
    tools = client.app.state.orchestrator.operator.tools

    async def fake_mic(device=""):
        return [tools._ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-ac", "1", "-b:a", "64k", "-f", "mp3", "pipe:1"]

    tools.live_mic_argv = fake_mic
    url = client.post("/api/live/mic").json()["url"]
    with client.stream("GET", url) as resp:
        assert resp.headers["content-type"] == "audio/mpeg"
        assert len(b"".join(resp.iter_bytes())) > 2000

    # talking: the phone's clip is converted and handed to the PC's player
    played = []
    real_proc = tools._proc
    async def quiet_player(argv, timeout):
        if Path(argv[0]).name.lower() in ("ffmpeg", "ffmpeg.exe"):
            return await real_proc(argv, timeout)
        played.append(argv)
        return 0, ""
    monkeypatch.setattr(tools, "_proc", quiet_player)
    real_which = __import__("shutil").which
    monkeypatch.setattr("hassan_ai.pc_tools.shutil.which", lambda n: "paplay" if n == "paplay" else real_which(n))
    clip = tmp_path / "clip.ogg"
    import subprocess
    subprocess.run([tools._ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", str(clip)], check=True)
    r = client.post("/api/intercom/say", content=clip.read_bytes(), headers={"Content-Type": "audio/ogg"})
    assert r.status_code == 200 and 0.8 < r.json()["seconds"] < 1.3
    assert played and ".wav" in str(played[-1])
    assert not list((tools.media_dir).glob(".say-*"))  # temp files cleaned
    assert client.post("/api/intercom/say", content=b"", headers={"Content-Type": "audio/ogg"}).status_code == 400


def test_dashboard_voice_goes_to_whisper(client, monkeypatch):
    async def fake(audio, name):
        assert audio == b"AUDIO" and name == "voice.webm"
        return " شو في على سطح المكتب "

    monkeypatch.setattr("hassan_ai.server.groq_transcriber", fake)
    r = client.post("/api/transcribe", content=b"AUDIO", headers={"Content-Type": "audio/webm;codecs=opus"})
    assert r.json() == {"text": "شو في على سطح المكتب"}


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_live_screen_is_view_only_stream(client):
    tools = client.app.state.orchestrator.operator.tools

    async def fake_screen(**_options):
        return [tools._ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i",
                "testsrc=size=640x360:rate=6:duration=1", "-f", "mpjpeg", "pipe:1"]

    tools.live_screen_argv = fake_screen
    assert client.get("/api/live/screen").status_code == 403  # no token, no screen
    url = client.post("/api/live/screen").json()["url"]
    with client.stream("GET", url) as resp:
        assert b"image/jpeg" in next(resp.iter_bytes())
    assert client.post("/api/live/nope").status_code == 404


def test_agent_can_show_the_files_it_made_but_not_other_private_data(tmp_path):
    t = tools(tmp_path)
    t.media_dir.mkdir(parents=True)
    video = t.media_dir / "kids-1.mp4"
    video.write_bytes(b"mp4")
    (tmp_path / "data" / "hassan.env").write_text("SECRET=1")
    assert t.access("open", {"target": str(video)}) == AUTO
    assert t.media_file("kids-1.mp4") == video.resolve()
    asyncio.run(t.execute("copy", {"src": str(video), "dst": str(tmp_path / "Desktop" / "kids.mp4")}))
    assert (tmp_path / "Desktop" / "kids.mp4").read_bytes() == b"mp4"
    for private in (str(tmp_path / "data" / "hassan.env"), str(t.media_dir / ".." / "hassan.env")):
        assert t.media_file(private) is None
        with pytest.raises(PolicyError):
            t.access("open", {"target": private})
