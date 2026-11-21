"""Container health check: exit 0 if the worker loop ran recently."""
import os
import sys
import time

from . import config

MAX_AGE = config.POLL_WAIT_SECONDS + 60

try:
    age = time.time() - os.path.getmtime(config.HEARTBEAT_FILE)
except OSError:
    sys.exit(1)
sys.exit(0 if age < MAX_AGE else 1)
