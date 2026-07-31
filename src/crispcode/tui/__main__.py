from __future__ import annotations

import argparse

from crispcode.core.config import get_config
from crispcode.tui.app import CrispTuiApp


# crisp-tui 入口：解析 --replay 参数后启动 TUI 应用
def main() -> None:
    parser = argparse.ArgumentParser(prog="crisp-tui", description="CrispCode TUI")
    parser.add_argument(
        "--replay",
        metavar="runs_id",
        help="Replay events from a past run on connect",
    )
    args = parser.parse_args()

    config = get_config()
    app = CrispTuiApp(config.host, config.port, replay_runs_id=args.replay)
    app.run()


if __name__ == "__main__":
    main()
