"""Allow running the package as ``python -m core``."""

import sys

from core.cli import main

sys.exit(main())
