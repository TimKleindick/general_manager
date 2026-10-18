"""Compatibility entry point for the historical saved-answer calibration."""

import sys

from .archive import calibrate_answer_judge as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

# Preserve module identity, including patches to globals such as AUDIT.
sys.modules[__name__] = _implementation
