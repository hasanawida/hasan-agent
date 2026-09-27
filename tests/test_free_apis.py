"""When the subscriptions run out, tasks keep running on free OpenAI-compatible APIs."""

import asyncio
import textwrap

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from hassan_ai.llm import MockLLM
from hassan_ai.providers import RouterLLM
from hassan_ai.server import create_app

from .conftest import make_settings, wait

CALLS: list[dict] = []


def fake_provider() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        CALLS.append({"model": body["model"], "auth": request.headers.get("authorization"),
                      "json": "response_format" in body})
        if request.headers.get("authorization") != "Bearer free-key":
            return _err(401, "invalid api key")
        if body["model"] == "retired-model":
            return _err(404, "model not found")
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        text = (await MockLLM().complete(body["model"], system, user)).text
        return {"model": body["model"], "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7}}

    return app


def _err(code, msg):
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": {"message": msg}}, status_code=code)


def providers_yaml(tmp_path, limited_claude: bool = True) -> str:
    cfg = tmp_path / "providers.yaml"
    cfg.write_text(textwrap.dedent("""
        backends:
          claude: {type: claude_cli, command: "%s"}
          chatgpt: {type: codex_cli, command: "%s"}
          groq: {type: api, url: "http://free.test/v1", key_env: HASSAN_TEST_GROQ, models: [retired-model, good-model]}
          gemini: {type: api, url: "http://free.test/v1", key_env: HASSAN_TEST_GEMINI, models: [g]}
        default: claude
        aliases: {reviewer: chatgpt, decision: chatgpt}
        fallback: [chatgpt, claude, gemini, groq]
    """ % ((tmp_path / "no-claude").as_posix(), (tmp_path / "no-codex").as_posix())))
    return str(cfg)


@pytest.fixture
def router(tmp_path, monkeypatch):
    CALLS.clear()
    monkeypatch.setenv("HASSAN_TEST_GROQ", "free-key")
    monkeypatch.delenv("HASSAN_TEST_GEMINI", raising=False)
    transport = httpx.ASGITransport(app=fake_provider())
    return RouterLLM.from_config(__import__("pathlib").Path(providers_yaml(tmp_path)), "http://x", "", 30,
                                 transport=transport)


def test_task_finishes_on_free_api_when_subscriptions_are_unavailable(tmp_path, router):
    settings = make_settings(tmp_path)
    settings.mode = "live"
    with TestClient(create_app(settings, llm=router)) as client:
        t = client.post("/api/tasks", json={"prompt": "keep going without Claude", "mode": "auto"}).json()
        task = wait(client, t["id"])
        assert task["status"] == "completed", task["error"]
        assert {o["model"] for o in task["outputs"]} == {"groq/good-model"}
        assert task["usage"]["input_tokens"] > 0
    # the retired model is tried, then the next model in the list; gemini (no key) is skipped
    assert CALLS[0]["model"] == "retired-model" and CALLS[1]["model"] == "good-model"
    assert all(c["auth"] == "Bearer free-key" for c in CALLS)
    assert not any(c["json"] for c in CALLS)  # free APIs get plain prompts unless json_mode: true


def test_keys_saved_from_dashboard_and_tested(tmp_path, router, monkeypatch):
    settings = make_settings(tmp_path)
    settings.mode = "live"
    with TestClient(create_app(settings, llm=router)) as client:
        keys = {k["name"]: k for k in client.get("/api/keys").json()}
        assert keys["groq"]["set"] and not keys["gemini"]["set"]
        assert "free-key" not in client.get("/api/keys").text  # keys are never echoed back
        # remote devices can neither read nor change keys
        phone = {"host": "pc.ts.net", "x-forwarded-for": "100.64.0.9"}
        assert client.get("/api/keys", headers=phone).status_code == 401
        client.cookies.set("hassan_key", client.app.state.access_key)
        assert client.get("/api/keys", headers=phone).status_code == 403
        client.cookies.clear()

        assert client.post("/api/keys", json={"name": "gemini", "key": "free-key"}).json()["set"]
        assert "HASSAN_TEST_GEMINI=free-key" in settings.env_file.read_text()
        r = client.post("/api/keys/gemini/test").json()
        assert r["ok"] and r["model"] == "gemini/g"
        client.post("/api/keys", json={"name": "gemini", "key": "wrong"})
        r = client.post("/api/keys/gemini/test").json()
        assert not r["ok"] and "401" in r["error"]
        client.post("/api/keys", json={"name": "gemini", "key": ""})
        assert "HASSAN_TEST_GEMINI" not in settings.env_file.read_text()
        assert client.post("/api/keys", json={"name": "gemini", "key": "has space"}).status_code == 400


def test_rate_limited_free_api_is_skipped(router):
    groq = router.backends["groq"]

    async def go():
        groq.limited_until = 10**12
        groq.limit_message = "429"
        with pytest.raises(Exception) as exc:
            await router.complete("manager", "ROLE: manager", "### TASK\nx")
        assert "rate limit" in str(exc.value)

    asyncio.run(go())
    assert CALLS == []  # nothing was sent while limited
