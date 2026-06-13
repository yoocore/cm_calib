from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _contains_block(lines: list[str], expected_block: list[str]) -> bool:
    window = len(expected_block)
    for index in range(0, len(lines) - window + 1):
        if lines[index : index + window] == expected_block:
            return True
    return False


@pytest.fixture
def cmctrl(monkeypatch):
    sys.modules.pop("cmapi_testrun_control", None)

    fake_cmapi = types.ModuleType("cmapi")
    fake_cmapi.error = types.SimpleNamespace(InvalidConfigurationError=RuntimeError)

    fake_runtime_bootstrap = types.ModuleType("runtime_config_bootstrap")
    fake_runtime_bootstrap.bootstrap_runtime_configs_for_cameras = MagicMock()
    fake_runtime_bootstrap.capture_initial_values_to_config = MagicMock()
    fake_runtime_bootstrap.load_movie_view_size_from_real_image = MagicMock()

    monkeypatch.setitem(sys.modules, "cmapi", fake_cmapi)
    monkeypatch.setitem(sys.modules, "runtime_config_bootstrap", fake_runtime_bootstrap)

    module = importlib.import_module("cmapi_testrun_control")
    yield module
    sys.modules.pop("cmapi_testrun_control", None)


def _capture_body_lines(monkeypatch, cmctrl, tmp_path: Path, detail: str) -> dict[str, list[str]]:
    captured: dict[str, list[str]] = {}

    def fake_render_dde_execute_script(_result_path, _target_topic, body_lines, **_kwargs):
        captured["body_lines"] = list(body_lines)
        return "script-text"

    def fake_run_check_attempt(*_args, **_kwargs):
        return {"ok": True, "detail": detail}

    monkeypatch.setattr(cmctrl, "default_output_dir", lambda: tmp_path)
    monkeypatch.setattr(cmctrl, "render_dde_execute_script", fake_render_dde_execute_script)
    monkeypatch.setattr(cmctrl, "run_check_attempt", fake_run_check_attempt)
    return captured


class TestCameraDialogActivationGuards:
    def test_ensure_movie_camera_selected_only_shows_settings_when_missing(self, cmctrl, monkeypatch, tmp_path):
        captured = _capture_body_lines(
            monkeypatch,
            cmctrl,
            tmp_path,
            "state=selected;selected=CAMERA_RSI-SENSOR Vhcl.cam1;current=CAMERA_RSI-SENSOR Vhcl.cam1;view=1;apply_invoked=1;capture_path=test.png",
        )

        cmctrl.ensure_movie_camera_selected("CAMERA_RSI-SENSOR Vhcl.cam1")

        body_lines = captured["body_lines"]
        assert sum(line.strip() == "Camera::ShowSettingsDlg" for line in body_lines) == 1
        assert _contains_block(
            body_lines,
            [
                'if {![winfo exists .camera] || ![winfo exists .camera.btn.set]} {',
                '    Camera::ShowSettingsDlg',
                '    update',
                '    update idletasks',
                '}',
            ],
        )

    def test_ensure_movie_camera_widgets_only_shows_settings_when_camera_missing(self, cmctrl, monkeypatch, tmp_path):
        captured = _capture_body_lines(
            monkeypatch,
            cmctrl,
            tmp_path,
            "before_camera=1;before_lens=1;after_camera=1;after_lens=1;lens_state=normal",
        )

        cmctrl.ensure_movie_camera_widgets()

        body_lines = captured["body_lines"]
        assert sum(line.strip() == "Camera::ShowSettingsDlg" for line in body_lines) == 1
        assert _contains_block(
            body_lines,
            [
                'if {!$before_camera} {',
                '    Camera::ShowSettingsDlg',
                '    update',
                '    update idletasks',
                '}',
            ],
        )

    def test_ensure_movie_camera_dialogs_only_shows_settings_when_camera_missing(self, cmctrl, monkeypatch, tmp_path):
        captured = _capture_body_lines(
            monkeypatch,
            cmctrl,
            tmp_path,
            "camera_exists=1;camera_title=Camera Settings;camera_state=iconic;lens_exists=1;lens_title=Lens Parameters;lens_state=normal",
        )

        cmctrl.ensure_movie_camera_dialogs_normal()

        body_lines = captured["body_lines"]
        assert sum(line.strip() == "Camera::ShowSettingsDlg" for line in body_lines) == 1
        assert _contains_block(
            body_lines,
            [
                'if {!$before_camera} {',
                '    Camera::ShowSettingsDlg',
                '    update',
                '    update idletasks',
                '}',
            ],
        )

class TestMovieAbraxasProbe:
    def test_ensure_movie_abraxas_enabled_avoids_forcing_render(self, cmctrl, monkeypatch, tmp_path):
        captured = _capture_body_lines(
            monkeypatch,
            cmctrl,
            tmp_path,
            "before=1;after=1;menu=.view0.mbar.view.m.show;view=0",
        )

        cmctrl.ensure_movie_abraxas_enabled()

        body_lines = captured["body_lines"]
        assert 'if {$before != 1} {$menu invoke 1}' in body_lines
        assert 'update' in body_lines
        assert 'update idletasks' in body_lines
        assert 'catch {UpdateView $View(ev.view)}' not in body_lines
        assert 'catch {event generate .view${vno}.gl0 <Expose>}' not in body_lines
        assert 'catch {UpdateView_TimerProc}' not in body_lines

class TestMovieEventPumpMitigations:
    def test_movie_background_tcl_commands_do_not_flush_event_loop(self, cmctrl):
        body_lines = cmctrl._movie_background_tcl_commands(include_root=True)
        assert 'catch {wm attributes . -topmost 0}' in body_lines
        assert 'catch {wm lower .}' in body_lines
        assert 'update' not in body_lines
        assert 'update idletasks' not in body_lines

    def test_ensure_movie_abraxas_enabled_raises_when_probe_does_not_latch(self, cmctrl, monkeypatch, tmp_path):
        _capture_body_lines(
            monkeypatch,
            cmctrl,
            tmp_path,
            "before=0;after=0;menu=.view0.mbar.view.m.show;view=0",
        )

        with pytest.raises(RuntimeError, match="ABRAXAS did not stay enabled"):
            cmctrl.ensure_movie_abraxas_enabled()


class TestEnsureMovieViewSize:
    def test_has_update_after_height_bump_to_stabilize_gl_context(self, cmctrl, monkeypatch, tmp_path):
        """Verify 'update' appears after height bump try-finally in ensure_movie_view_size.

        Without this 'update', the GL context remains unstable after 2x View::SetSize
        resize. When the subsequent capture body calls UpdateView, IPG-MOVIE's internal
        FBO operations fail with 'FBO error: id not mapped'.
        """
        captured = _capture_body_lines(monkeypatch, cmctrl, tmp_path, "ok")
        # Call ensure_movie_view_size with standard dimensions
        cmctrl.ensure_movie_view_size(960, 640)
        body_lines = captured["body_lines"]

        # Find height bump restore (end of try-finally), cancel timer, and update
        hb_restore = [l for l in body_lines if "rename CheckViewPort_saved CheckViewPort" in l]
        cancel_lines = [l for l in body_lines if "after cancel UpdateView_TimerProc" in l]
        update_lines = [l for l in body_lines if l.strip() == "update"]
        assert len(hb_restore) >= 1, "Expected at least one height bump restore line"
        assert len(cancel_lines) >= 1, "Expected at least one 'after cancel UpdateView_TimerProc' line"
        assert len(update_lines) >= 1, "Expected at least one 'update' after height bump"

        # Verify ordering: height bump finished -> cancel timer -> update
        hb_restore_idx = next(i for i, l in enumerate(body_lines)
                              if "rename CheckViewPort_saved CheckViewPort" in l)
        cancel_idx = next(i for i, l in enumerate(body_lines)
                          if "after cancel UpdateView_TimerProc" in l)
        update_idx = next(i for i, l in enumerate(body_lines)
                          if l.strip() == "update" and "after cancel" not in l)
        assert hb_restore_idx < cancel_idx, \
            f"cancel timer (idx={cancel_idx}) must come after height bump restore (idx={hb_restore_idx})"
        assert cancel_idx < update_idx, \
            f"'update' (idx={update_idx}) must come after cancel timer (idx={cancel_idx})"

        # Verify 'update idletasks' is NOT used (known to cause FBO Creation errors)
        assert "update idletasks" not in body_lines


class TestCheckViewPortRecursionGuard:
    def test_disable_sends_guarded_wrapper(self, cmctrl, monkeypatch, tmp_path):
        captured = _capture_body_lines(monkeypatch, cmctrl, tmp_path, "ok")
        cmctrl.disable_checkviewport_recursion()
        body_lines = captured["body_lines"]
        # disable_checkviewport_recursion now uses the guarded wrapper (::ReGuardCheckViewPort)
        # instead of the simple no-op. Verify the guarded wrapper pattern.
        assert any("::ReGuardCheckViewPort" in l for l in body_lines)
        assert any("CheckViewPort_running" in l for l in body_lines)
        assert any("CheckViewPort_saved" in l for l in body_lines)

    def test_restore_sends_rename_back(self, cmctrl, monkeypatch, tmp_path):
        captured = _capture_body_lines(monkeypatch, cmctrl, tmp_path, "ok")
        cmctrl.restore_checkviewport()
        body_lines = captured["body_lines"]
        assert "catch {rename CheckViewPort {}}" in body_lines
        assert "catch {rename CheckViewPort_saved CheckViewPort}" in body_lines

    def test_disable_is_non_fatal_on_dde_failure(self, cmctrl, monkeypatch, tmp_path):
        monkeypatch.setattr(cmctrl, "default_output_dir", lambda: tmp_path)
        monkeypatch.setattr(cmctrl, "render_dde_execute_script", lambda *a, **kw: "script")
        monkeypatch.setattr(cmctrl, "run_check_attempt", lambda *a, **kw: {"ok": False, "detail": "timeout"})
        cmctrl.disable_checkviewport_recursion()

    def test_restore_is_non_fatal_on_dde_failure(self, cmctrl, monkeypatch, tmp_path):
        monkeypatch.setattr(cmctrl, "default_output_dir", lambda: tmp_path)
        monkeypatch.setattr(cmctrl, "render_dde_execute_script", lambda *a, **kw: "script")
        monkeypatch.setattr(cmctrl, "run_check_attempt", lambda *a, **kw: {"ok": False, "detail": "timeout"})
        cmctrl.restore_checkviewport()
