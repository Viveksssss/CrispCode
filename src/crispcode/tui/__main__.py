from __future__ import annotations

import argparse
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from crispcode.core.config import get_config
from crispcode.tui.app import CrispTuiApp

_DEFAULT_TUI_LOG = "~/.crispcode/logs/tui.log"


# TUI 文件日志初始化：不写 stderr（避免干扰 Textual 渲染），只写滚动文件
def _setup_logging(level: str) -> None:
    log_path = Path(os.environ.get("CRISP_TUI_LOG_FILE", _DEFAULT_TUI_LOG)).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            'level=%(levelname)s ts=%(asctime)s source=%(name)s msg="%(message)s"',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    root.handlers.clear()
    root.addHandler(handler)


# 检测 daemon 是否已在指定地址上监听
def _daemon_alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0) as s:
            s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


# 启动 daemon 子进程并等待它就绪；返回 Popen 对象（None 表示 daemon 本已在跑）
def _ensure_daemon(host: str, port: int) -> subprocess.Popen | None:
    if _daemon_alive(host, port):
        return None

    proc = subprocess.Popen(
        [sys.executable, "-m", "crispcode.core"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # 独立进程组，TUI 退出时 daemon 不受影响
    )

    # 等待 daemon 就绪，最多 10 秒
    for _ in range(50):
        time.sleep(0.2)
        if _daemon_alive(host, port):
            return proc
        if proc.poll() is not None:
            # daemon 进程已退出，启动失败
            raise RuntimeError(
                f"crisp-core exited immediately (code={proc.returncode}). "
                "Run 'crisp-core' manually to see the error."
            )

    # 超时：杀掉子进程，避免僵尸
    proc.kill()
    proc.wait()
    raise RuntimeError(
        f"crisp-core did not become ready on {host}:{port} within 10 seconds."
    )


# crisp-tui 入口：自动拉起 daemon（若需要），然后启动 TUI
def main() -> None:
    parser = argparse.ArgumentParser(prog="crisp-tui", description="CrispCode TUI")
    parser.add_argument(
        "--replay",
        metavar="runs_id",
        help="Replay events from a past run on connect",
    )
    args = parser.parse_args()

    config = get_config()

    # 自动启动 daemon（如果尚未运行）
    daemon_proc = _ensure_daemon(config.host, config.port)

    app = CrispTuiApp(config, replayed_runs_id=args.replay)
    try:
        app.run()
    finally:
        # 如果是本次启动的 daemon，TUI 退出后一并关闭
        if daemon_proc is not None:
            daemon_proc.terminate()
            try:
                daemon_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()
                daemon_proc.wait()


if __name__ == "__main__":
    main()
