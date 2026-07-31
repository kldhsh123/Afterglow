#!/usr/bin/env python3
"""关系分析 CLI 的薄封装。"""

from __future__ import annotations

import sys

from xuwen.ingestion.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    return cli_main(["analyze", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
