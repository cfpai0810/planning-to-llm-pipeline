# =============================================================================
# main.py: the command line (a stub in Phase 0)
# =============================================================================
"""
The pipeline's command line. Phase 0 builds the shared kit only, so this says
so and stops.
"""

from __future__ import annotations

import sys
from typing import Optional

NOT_YET = "Phase 0, not yet usable: the pipeline arrives in a later phase."


def main(argv: Optional[list[str]] = None) -> int:
    print(NOT_YET)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
