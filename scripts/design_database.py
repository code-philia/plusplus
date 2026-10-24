"""Standalone design database entry; execution belongs to compiler stages."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cli.design import main as run_command


def main(argv=None):
    return run_command('database', argv)


if __name__ == '__main__':
    raise SystemExit(main())
