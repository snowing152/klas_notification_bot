"""How much memory the process holds, for the log.

On Railway the bill is almost all RAM, and the Metrics tab only shows the
container as a whole: it cannot say whether a peak was a notification cycle,
an image for /qr or /info, or the imports at startup. These two numbers go on
the lines that can.

Both read what the kernel already keeps, so they cost nothing and need no
dependency (psutil would itself add to the number it reports). Each returns
None where the source does not exist - /proc outside Linux, `resource` on
Windows - and the caller leaves the figure out of the line.

The numbers are the Python process only. Railway measures the container, which
also counts the kernel's page cache for it, so its figure is the higher one;
what to compare is when the two move, not their size.
"""

import os
import sys
from typing import Optional

try:
    import resource
except ImportError:  # Windows
    resource = None

STATM_PATH = "/proc/self/statm"
MB = 1024 * 1024


def rss_mb() -> Optional[float]:
    """Resident memory right now, in MB."""
    try:
        with open(STATM_PATH) as statm:
            # Fields are in pages: total size, resident, shared, ...
            resident_pages = int(statm.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return resident_pages * os.sysconf("SC_PAGE_SIZE") / MB


def peak_rss_mb() -> Optional[float]:
    """The most resident memory the process has held since it started, in MB.

    It never goes down, so it says how high the process has been, not where it
    is now: Python rarely hands freed memory back to the system.
    """
    if resource is None:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Kilobytes on Linux, bytes on macOS.
    return peak / MB if sys.platform == "darwin" else peak / 1024
