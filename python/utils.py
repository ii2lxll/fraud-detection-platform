"""Small helpers shared by several scripts."""
from pathlib import Path

import pandas as pd


def read_daily_files(folder: str | Path, pattern: str = "*.csv") -> pd.DataFrame:
    """Read and stack all daily files in a folder (sorted by name, so by date)."""
    files = sorted(Path(folder).glob(pattern))
    if not files:
        raise FileNotFoundError(f"No files matching {pattern} in {folder}")
    if pattern.endswith(".jsonl"):
        return pd.concat((pd.read_json(f, lines=True, dtype=False) for f in files), ignore_index=True)
    return pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
