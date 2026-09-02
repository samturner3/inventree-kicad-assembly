#!/usr/bin/env python3
"""CLI launcher.

Exists so the command line can use the package without KiCad's plugin
registration running: it sets the opt-out before the package is imported,
which `python -m inventree_kicad_assembly.cli` cannot do, since that imports
__init__ first.

Run under any Python 3.9+, including KiCad's own:

    python3 inventree_assembly_cli.py --build-order 5
"""

import os
import sys

os.environ["INVENTREE_KICAD_ASSEMBLY_NO_REGISTER"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from inventree_kicad_assembly.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
