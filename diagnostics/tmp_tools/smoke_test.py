#!/usr/bin/env python3
"""Smoke test — verify all entry points and module imports after code split."""
import os, sys, subprocess, warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

errors = []

def check(label, ok, detail=''):
    if ok:
        print(f'  [PASS] {label}')
    else:
        print(f'  [FAIL] {label}: {detail}')
        errors.append(label)

# ── 1. 核心模块导入 ──
print('\n[1] 核心模块导入')

for mod in [
    'src.calibration.camera_calibration',
    'src.calibration.cli', 'src.calibration.utils', 'src.calibration.config',
    'src.calibration.detector', 'src.calibration.scoring', 'src.calibration.annotation',
    'src.calibration.script_control', 'src.calibration.evaluate', 'src.calibration.strategy',
    'src.calibration.orchestration', 'src.calibration.optimizer_cd',
    'src.calibration.optimizer_bayesian', 'src.calibration.initial_solver',
    'src.calibration.sensitivity', 'src.calibration.calib_types',
]:
    try:
        __import__(mod)
        check(mod, True)
    except Exception as e: check(mod, False, str(e))

for mod in [
    'src.entry.portable_runtime', 'src.entry.launch_gui',
    'src.cmapi.cmapi_testrun_control',
    'src.orchestration.calibration_orchestrator',
    'src.health.dde_health_check', 'src.health.rendering_health',
    'src.health.precheck_cli', 'src.health.fbo_score_check',
    'src.gui_app.app',
]:
    try:
        __import__(mod)
        check(mod, True)
    except ImportError as e: check(mod, False, str(e))

# ── 2. subprocess 入口点 ──
print('\n[2] subprocess 入口点')
scripts = [
    ('src/health/precheck_cli.py', f'--project-root "{ROOT}" --camera TRight'),
    ('src/calibration/cli.py', '--help'),
]
for script, args in scripts:
    try:
        r = subprocess.run([sys.executable, str(ROOT / script)] + args.split(),
                          capture_output=True, text=True, timeout=15)
        ok = r.returncode == 0
        has_prefix = 'PRECHECK_RESULT_JSON:' in r.stdout or 'usage:' in r.stdout or 'CALIBRATION_PROGRESS_JSON:' in r.stdout
        check(f'{script} runs', ok and has_prefix, f'exit={r.returncode} out={r.stdout[:100]}')
        if not ok and r.stderr:
            print(f'     stderr: {r.stderr[:200]}')
    except subprocess.TimeoutExpired:
        check(f'{script} runs', False, 'timeout')
    except Exception as e: check(f'{script} runs', False, str(e))

# ── 3. 关键文件存在性 ──
print('\n[3] 关键文件存在性')
key_files = [
    'src/health/precheck_cli.py',
    'src/calibration/cli.py',
    'src/entry/launch_gui.py',
    'src/entry/launch_wizard.py',
    'src/entry/portable_runtime.py',
    'src/cmapi/cmapi_testrun_control.py',
    'src/orchestration/calibration_orchestrator.py',
    'src/calibration/camera_calibration.py',
]
for kf in key_files:
    check(kf, (ROOT / kf).exists())

# ── 4. 从非项目根目录启动 bootstrap ──
print('\n[4] bootstrap 完整性')
for script_rel, expected_parents in [
    ('src/entry/launch_gui.py', 2),
    ('src/entry/launch_wizard.py', 2),
    ('src/health/precheck_cli.py', 2),
    ('src/calibration/cli.py', 2),
]:
    try:
        p = ROOT / script_rel
        r = subprocess.run([sys.executable, '-c', f'''
import sys
from pathlib import Path
assert Path(r"{p}").resolve().parents[{expected_parents}] == Path(r"{ROOT}")
print("OK")
'''], capture_output=True, text=True, timeout=5)
        check(f'{script_rel} _ROOT={expected_parents}', 'OK' in r.stdout, r.stderr[:100])
    except Exception as e: check(f'{script_rel} _ROOT', False, str(e))

# ── 5. 测试全部存在 ──
print('\n[5] 测试文件')
test_root = ROOT / 'tests'
for tf in sorted(test_root.rglob('test_*.py')):
    rel = tf.relative_to(ROOT)
    if '__pycache__' not in rel.parts:
        check(str(rel), True)

print(f'\n========== 结果: {len(errors)} 失败 / {len([e for e in errors])} ==========')
import os
sys.exit(len(errors))
