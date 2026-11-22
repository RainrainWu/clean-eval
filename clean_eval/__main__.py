"""Entry point: `uv run python -I <clean-eval checkout>/clean_eval <command> ...` (any interpreter with the locked
dependencies will do).

`-I` keeps the current folder (the repository being rated) and PYTHONPATH off sys.path; only the checkout that holds
this package is added back, so nothing from the rated repository can be imported."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clean_eval.external.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
