"""Minimal DDE test."""
import time, uuid
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "tmp" / "view_check"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

import win32ui  # noqa: F401
import dde  # type: ignore

result_file = OUTPUT_DIR / "simple_result.txt"
script_file = OUTPUT_DIR / "simple_test.tcl"

tcl = ' '.join([
    f'set fp [open "{result_file.as_posix()}" w];',
    'puts $fp "step1"; flush $fp;',
    'package require dde;',
    'puts $fp "step2"; flush $fp;',
    'set rc [catch { dde execute TclEval CarMaker { winfo exists . } } msg];',
    'puts $fp "dde_rc=$rc";',
    'puts $fp "dde_msg=$msg";',
    'flush $fp; close $fp',
])

script_file.write_text(tcl, encoding="utf-8")
try:
    result_file.unlink()
except FileNotFoundError:
    pass

server = dde.CreateServer()
server.Create(f"SimpleTest.{uuid.uuid4().hex}")
conv = dde.CreateConversation(server)
conv.ConnectTo("TclEval", "CarMaker")
print("Connected")
conv.Exec(f"RunScript {{{script_file.as_posix()}}}")
print("Executed")

deadline = time.time() + 10
while time.time() < deadline:
    if result_file.exists():
        print(f"Result:\n{result_file.read_text(encoding='utf-8')}")
        break
    time.sleep(0.3)
else:
    print("Timeout!")

server.Shutdown()
