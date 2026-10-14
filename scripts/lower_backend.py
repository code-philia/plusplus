"""Lower backend contracts to grouped TypeScript/Express skeletons."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from compiler.lowering.cli import main

if __name__ == '__main__':
    raise SystemExit(main('backend'))
