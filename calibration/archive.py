"""
Timestamped archive copies of pipeline outputs, with a retention limit.

Every calibration run keeps a dated copy of each stage output under
`datasets/archive/` so a previous run is never silently overwritten. The
stage-1 output is around 1 GB per run, so copies are pruned: only the newest
`DEFAULT_KEEP` copies of each prefix survive, older ones are deleted.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

# How many timestamped copies to keep per output file
DEFAULT_KEEP = 2

_TS_FORMAT = "%Y-%m-%d_%H-%M-%S"
_ARCHIVE_SUFFIX = "__"


def build_archive_path(archive_dir: str, prefix: str, when: datetime | None = None) -> str:
    """Return the path for a new timestamped copy, creating the directory."""
    os.makedirs(archive_dir, exist_ok=True)
    ts = (when or datetime.now()).strftime(_TS_FORMAT)
    return os.path.join(archive_dir, f"{prefix}{_ARCHIVE_SUFFIX}{ts}.csv")


def prune_archive(archive_dir: str, prefix: str, keep: int = DEFAULT_KEEP) -> list[str]:
    """
    Delete all but the newest `keep` archived copies of `prefix`.

    Files are ordered by the timestamp in their name rather than by filesystem
    mtime, so copying the archive elsewhere does not change what gets pruned.
    Returns the paths that were removed.
    """
    if keep < 1 or not os.path.isdir(archive_dir):
        return []

    pattern = re.compile(
        rf"^{re.escape(prefix)}{re.escape(_ARCHIVE_SUFFIX)}"
        r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.csv$"
    )

    dated = []
    for name in os.listdir(archive_dir):
        m = pattern.match(name)
        if m:
            dated.append((m.group(1), os.path.join(archive_dir, name)))

    dated.sort(reverse=True)  # newest first

    removed = []
    for _, path in dated[keep:]:
        try:
            os.remove(path)
            removed.append(path)
        except OSError as exc:
            print(f"  [WARN] Could not remove old archive {os.path.basename(path)}: {exc}")

    return removed


def archive_dataframe(df, archive_dir: str, prefix: str, keep: int = DEFAULT_KEEP) -> str:
    """
    Write a timestamped copy of `df` and prune older copies.

    Returns the path that was written.
    """
    path = build_archive_path(archive_dir, prefix)
    df.to_csv(path, index=False)

    for removed in prune_archive(archive_dir, prefix, keep=keep):
        print(f"  [ARCHIVE] Removed old copy: {os.path.basename(removed)}")

    return path
