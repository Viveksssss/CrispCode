from __future__ import annotations


import argparse
import sys


from crispcode.cli.commands.ping import cmd_ping
from crispcode.cli.commands.version import cmd_version
from crispcode.core.config import get_config
from crispcode.core.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="crisp", description="CrispCode CLI")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("ping", help="Ping the core daemon")

    args = parser.parse_args()

    if args.version:
        cmd_version()
        return
    if args.command == "ping":
        config = get_config()
        setup_logging(config)
        cmd_ping(config)
    else:
        parser.print_help()
        sys.exit(1)
