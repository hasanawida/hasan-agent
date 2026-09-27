"""Bounded screen capture commands; no capture process or OS input is started."""
import asyncio
from types import SimpleNamespace

import pytest

from hassan_ai import pc_tools
from hassan_ai.pc_tools import PCTools, list_monitors, resolve_screen_capture, screen_profiles


@pytest.fixture
def displays(monkeypatch):
    values = [
        {"id": "main", "label": "Main", "left": 0, "top": 0, "width": 1920, "height": 1080, "primary": True},
        {"id": "left", "label": "Left", "left": -1600, "top": -120, "width": 1600, "height": 900, "primary": False},
    ]
    monkeypatch.setattr(pc_tools, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(pc_tools, "_windows_monitors", lambda: [dict(item) for item in values])
    monkeypatch.setattr(PCTools, "_ffmpeg", staticmethod(lambda: "ffmpeg-test"))
    return values


def test_monitors_include_virtual_union_with_negative_origins(displays):
    virtual, primary, left = list_monitors()
    assert virtual == {"id": "desktop", "label": "كل الشاشات", "left": -1600, "top": -120,
                       "width": 3520, "height": 1200, "primary": False}
    assert primary["primary"] and left["left"] == -1600
    resolved = resolve_screen_capture("balanced", "left")
    assert resolved["monitor"] == left
    assert resolved["virtual"] == virtual


@pytest.mark.parametrize("profile,monitor", [("unknown", "desktop"), ("balanced", "disconnected"),
                                           ("-vf arbitrary", "desktop"), ("sharp", "desktop;bad")])
def test_unknown_profile_and_monitor_are_rejected_before_command(displays, profile, monitor):
    pc = object.__new__(PCTools)
    with pytest.raises(ValueError):
        asyncio.run(pc.live_screen_argv(profile, monitor))


@pytest.mark.parametrize("profile", ["economy", "balanced", "sharp"])
def test_profile_capture_bounds_both_axes_and_uses_known_display_only(displays, profile):
    pc = object.__new__(PCTools)
    argv = asyncio.run(pc.live_screen_argv(profile, "left"))
    metadata = next(item for item in screen_profiles() if item["id"] == profile)
    assert argv[0] == "ffmpeg-test"
    assert argv[argv.index("-framerate") + 1] == str(metadata["fps"])
    assert argv[argv.index("-offset_x") + 1] == "-1600"
    assert argv[argv.index("-offset_y") + 1] == "-120"
    assert argv[argv.index("-video_size") + 1] == "1600x900"
    scale = argv[argv.index("-vf") + 1]
    assert f"min({metadata['max_width']},iw)" in scale  # no upscaling
    assert f"min({metadata['max_height']},ih)" in scale  # portrait/ultrawide bounded too
    assert "force_original_aspect_ratio=decrease:force_divisible_by=2" in scale
    assert argv[argv.index("-q:v") + 1] == str(metadata["jpeg_quality"])
    assert argv[-3:] == ["-f", "mpjpeg", "pipe:1"]
    assert metadata["fps"] <= 15 and metadata["max_width"] <= 1920 and metadata["max_height"] <= 1080


def test_profile_metadata_cannot_mutate_the_allowed_capture_settings(displays):
    profiles = screen_profiles()
    profiles[0]["fps"] = 500
    profiles[0]["max_width"] = 100000
    resolved = resolve_screen_capture("economy", "desktop")
    assert resolved["profile"]["fps"] == 6
    assert resolved["profile"]["max_width"] == 960


def test_x11_default_preserves_the_existing_capture_source(monkeypatch):
    monkeypatch.setattr(pc_tools, "os", SimpleNamespace(name="posix", environ={"DISPLAY": ":7"}))
    monkeypatch.setattr(PCTools, "_ffmpeg", staticmethod(lambda: "ffmpeg-test"))
    pc = object.__new__(PCTools)
    argv = asyncio.run(pc.live_screen_argv())
    assert argv[argv.index("-i") + 1] == ":7"
    assert "x11grab" in argv and "-offset_x" not in argv
    assert len(list_monitors()) == 1
    with pytest.raises(ValueError):
        asyncio.run(pc.live_screen_argv("balanced", "main"))
