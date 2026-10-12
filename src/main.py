"""ARC command-line entry point."""

import sys

from cli.commands import main


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] not in {"compile", "config", "doctor", "--version"}:
        sys.argv.insert(1, "compile")
    main()
