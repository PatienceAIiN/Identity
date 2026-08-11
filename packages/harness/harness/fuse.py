"""Re-export: canonical implementation lives in packages/encoder."""
from encoder.fusion import *  # noqa: F401,F403
from encoder.fusion import (FuseParams, FusedResult, fuse, make_payload,  # noqa: F401
                            CONTRAST_LEVELS, PLACEMENTS, QUIET_MODULES, _cover_crop)
