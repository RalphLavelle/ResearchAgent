r"""Print read-only recursive self-improvement diagnostics.

Run from the repository root:

    .\venv\Scripts\python.exe scripts\diag_strategy.py
"""

from __future__ import annotations

from agent import config
from agent.strategy_diagnostics import (
    build_strategy_diagnostics,
    format_strategy_diagnostics,
)


def main() -> None:
    diagnostics = build_strategy_diagnostics(config.ACTIVE_TOPIC.db)
    print(format_strategy_diagnostics(diagnostics))


if __name__ == "__main__":
    main()
