"""Quiet Prefect's own logging before it is imported.

Prefect's temporary server logs "Stopping temporary server" through a rich
console at interpreter shutdown, by which point pytest has closed the stream it
writes to. The result is a `ValueError: I/O operation on closed file` traceback
after a run that actually passed, which makes a green suite look broken.

Raising the level suppresses that INFO record. Set here rather than in a test
module because conftest is imported first, before anything pulls in Prefect.
"""

import os

os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
