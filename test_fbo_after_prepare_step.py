"""
隔离测试：逐步执行 prepare 步骤，每步后测 FBO。
找出哪个步骤破坏了 GL context。
"""
import sys
sys.path.insert(0, r"C:\CM_Projects\CMO141_Calibration\Data\Script\CameraCalibration")

from cmapi_testrun_control import (
    ensure_movie_view_size,
    ensure_movie_abraxas_enabled,
    ensure_movie_camera_selected,
    ensure_movie_camera_widgets,
    run_movie_send_health_check,
    render_dde_execute_script, run_check_attempt,
    default_output_dir,
)

OUT = default_output_dir(); OUT.mkdir(parents=True, exist_ok=True)

def test_fbo(step_name):
    result = run_check_attempt(name="fbo_test", service="TclEval", topic="CarMaker",
        output_dir=OUT,
        script_text=render_dde_execute_script(OUT/"fbo_test.txt", "IPG-MOVIE", [
            "scan $View(ev.view) %d vno",
            'set wpath ".view$vno"',
            "set wi [$wpath.gl0 cget -width]",
            "set he [$wpath.gl0 cget -height]",
            "set captureFBO [FBO new $wi $he -tex rgb -noclear]",
            "FBO begin $captureFBO",
            "UpdateView $vno",
            "FBO end",
            "catch {FBO delete $captureFBO}",
            'format "ok=1;fbo_size=%sx%s" $wi $he',
        ]), timeout_sec=10.0)
    ok = result.get("ok", False)
    print(f"  [{step_name}] FBO: {'OK' if ok else 'FAILED'}: {str(result.get('detail',''))[:100]}")

# 依次测试
print("1. 基线"); test_fbo("baseline")
print("2. ensure_movie_view_size(960,640)"); ensure_movie_view_size(960,640); test_fbo("view_size")
print("3. ensure_movie_abraxas_enabled"); ensure_movie_abraxas_enabled(); test_fbo("abraxas")
print("4. ensure_movie_camera_selected (skip_fbo_probe=True)")
ensure_movie_camera_selected("right_rear", timeout_sec=8.0, skip_fbo_probe=True); test_fbo("camera_sel")
print("5. ensure_movie_camera_widgets"); ensure_movie_camera_widgets(); test_fbo("widgets")
print("6. health check"); run_movie_send_health_check(1, 8.0, 0.3); test_fbo("health")
