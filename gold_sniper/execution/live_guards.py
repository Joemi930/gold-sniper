"""Live execution facade for the shared pure guard rules.

The canonical guard implementation lives in safety.live_guards so replay can
reuse it without importing the live execution package. This module preserves
the deployment-spec import path for the live trade manager.
"""

from safety.live_guards import *  # noqa: F401,F403
