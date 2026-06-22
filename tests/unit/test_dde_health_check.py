from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def dde_module():
    module = importlib.import_module("src.health.dde_health_check")
    module = importlib.reload(module)
    return module


class TestRenderDdeExecuteScript:
    def test_render_dde_execute_script_waits_for_remote_result(self, dde_module):
        script = dde_module.render_dde_execute_script(
            Path(r"C:\\temp\\probe.txt"),
            "IPG-MOVIE",
            ['format "ok=%s" 1'],
        )

        assert 'set __copilot_remote_wait_deadline [expr {[clock milliseconds] + 1000}]' in script
        assert 'while {![file exists $__copilot_remote_result_path] && [clock milliseconds] < $__copilot_remote_wait_deadline} {' in script
        assert 'after 25' in script
        assert 'if {[file exists $__copilot_remote_result_path]} {' in script