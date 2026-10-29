"""Allow ``python -m soroban_indexer``."""

import sys

from .cli import main

sys.exit(main())
