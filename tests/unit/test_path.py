from pathlib import Path

RUNS_DIR = Path.home() / ".crispcode" / "projects"


def test_path():
    print("path:", Path.cwd())


def test_run_dir() -> Path:
    """
    将当前项目的目录转换成特定格式字符串
    例如 /home/user/pro -> home-user-pro(path)
    然后将持久化文件存放入指定地点: ~/.crispcode/projects/$(path)/runs_id/events.jsonl
    """

    parts = Path.cwd().parts[1:]
    path = "-".join(parts)
    print(Path.cwd())
    print(Path.cwd())
    print(Path.home())
    print(RUNS_DIR)
    print(RUNS_DIR / path)
    print(RUNS_DIR / path / "asdasdsada")
