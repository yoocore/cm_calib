#!/usr/bin/env python3
"""自动监控配置文件变化并重新生成HTML"""

import time
import subprocess
from pathlib import Path

def generate():
    subprocess.run(["python", "generate_html_doc.py"], capture_output=True)

def watch():
    config_file = Path("doc_config.json")
    last_modified = config_file.stat().st_mtime

    print("[监控模式启动]")
    print(f"  配置文件: {config_file.absolute()}")
    print(f"  按 Ctrl+C 停止监控")
    print("-" * 50)

    generate()
    print("[OK] 初始生成完成\n")

    try:
        while True:
            current_modified = config_file.stat().st_mtime
            if current_modified != last_modified:
                print("[检测到配置变化，重新生成...]")
                generate()
                print("[OK] 生成完成 - 刷新浏览器查看效果\n")
                last_modified = current_modified
            time.sleep(0.5)  # 检查频率：0.5秒
    except KeyboardInterrupt:
        print(f"\n[监控已停止]")

if __name__ == "__main__":
    watch()
