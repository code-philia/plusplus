"""Standalone lower backend entry; execution belongs to compiler stages."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cli.stage_commands import lower_main as main


if __name__ == '__main__':
    raise SystemExit(main('backend'))
