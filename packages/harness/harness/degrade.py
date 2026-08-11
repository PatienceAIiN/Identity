"""Re-export: canonical implementation lives in packages/encoder."""
from encoder.degrade import *  # noqa: F401,F403
from encoder.degrade import (JPEG_QUALITIES, ROTATIONS_DEG, BRIGHTNESS, SCALES,  # noqa: F401
                             BLUR_SIGMAS, NOMINAL, Condition, axis_conditions,
                             gate_conditions, stress_conditions, all_conditions, apply)
