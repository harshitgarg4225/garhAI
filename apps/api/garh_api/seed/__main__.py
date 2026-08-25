"""``python -m garh_api.seed`` — see :mod:`garh_api.seed.runner` for the flags."""

from __future__ import annotations

import sys

from garh_api.seed.runner import main

if __name__ == "__main__":
    sys.exit(main())
