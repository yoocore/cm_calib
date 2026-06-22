from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def fbo_module(monkeypatch):
    module = importlib.import_module("src.health.fbo_score_check")
    module = importlib.reload(module)
    return module


def _capture_call(monkeypatch, fbo_module, tmp_path: Path, *, stage: str) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_render_dde_execute_script(result_path, _target_topic, body_lines, **_kwargs):
        captured["result_path"] = result_path
        captured["body_lines"] = list(body_lines)
        return "script-text"

    def fake_run_check_attempt(name, *_args, **_kwargs):
        captured["name"] = name
        capture_path = tmp_path / "fbo_capture.png"
        capture_path.write_bytes(b"png")
        return {"rc": 0, "ok": True, "detail": "ok"}

    monkeypatch.setattr(fbo_module, "render_dde_execute_script", fake_render_dde_execute_script)
    monkeypatch.setattr(fbo_module, "run_check_attempt", fake_run_check_attempt)
    fbo_module.capture_fbo(tmp_path, stage=stage)
    return captured


class TestCaptureFboStages:
    def test_capture_fbo_stage_new_stops_before_begin(self, monkeypatch, fbo_module, tmp_path: Path):
        captured = _capture_call(monkeypatch, fbo_module, tmp_path, stage="new")
        body_lines = captured["body_lines"]

        assert "set captureFBO [FBO new $wi $he -tex rgb -noclear]" in body_lines
        assert "    FBO begin $captureFBO" not in body_lines
        assert "    UpdateView $vno" not in body_lines
        assert "gl readpixels 0 0 probeImg" not in body_lines

    def test_capture_fbo_stage_begin_end_wraps_without_update(self, monkeypatch, fbo_module, tmp_path: Path):
        captured = _capture_call(monkeypatch, fbo_module, tmp_path, stage="begin_end")
        body_lines = captured["body_lines"]

        assert "set captureFBO [FBO new $wi $he -tex rgb -noclear]" in body_lines
        assert "    FBO begin $captureFBO" in body_lines
        assert "    UpdateView $vno" not in body_lines
        assert "    FBO end" in body_lines
        assert "gl readpixels 0 0 probeImg" not in body_lines

    def test_capture_fbo_stage_update_adds_update_without_readpixels(self, monkeypatch, fbo_module, tmp_path: Path):
        captured = _capture_call(monkeypatch, fbo_module, tmp_path, stage="update")
        body_lines = captured["body_lines"]

        assert "    FBO begin $captureFBO" in body_lines
        assert "    UpdateView $vno" in body_lines
        assert "    FBO end" in body_lines
        assert "gl readpixels 0 0 probeImg" not in body_lines

    def test_capture_fbo_stage_readpixels_includes_full_capture_flow(self, monkeypatch, fbo_module, tmp_path: Path):
        captured = _capture_call(monkeypatch, fbo_module, tmp_path, stage="readpixels")
        body_lines = captured["body_lines"]

        assert "    FBO begin $captureFBO" in body_lines
        assert "    UpdateView $vno" in body_lines
        assert "gl readpixels 0 0 probeImg" in body_lines
        assert any(line.startswith('probeImg write "') for line in body_lines)

    def test_capture_fbo_stage_names_match_script_result_paths(self, monkeypatch, fbo_module, tmp_path: Path):
        captured = _capture_call(monkeypatch, fbo_module, tmp_path, stage="begin_end")

        assert captured["name"] == "fbo_capture_begin_end"
        assert Path(captured["result_path"]).name == "fbo_capture_begin_end.txt"
