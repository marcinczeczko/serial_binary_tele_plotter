"""
Export of captured samples to files (R4.3). Pure numpy, no Qt.

One table per stream: `time_s` first, then one column per signal (named by signal id,
the stable key in streams.json). Rows where every signal is NaN are left out. Those are
the time base's gap markers, not samples. A value missing for a single signal stays
missing: an empty CSV field, or a null in Parquet.

CSV needs nothing extra. Parquet uses `pyarrow` when it's installed (an optional
dependency: `uv sync --extra parquet`).
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np

FORMATS = ("csv", "parquet")


class ExportError(Exception):
    """The table can't be written (unknown format, missing optional dependency, I/O)."""


def parquet_available() -> bool:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


def select_rows(
    time: np.ndarray,
    columns: dict[str, np.ndarray],
    x_range: tuple[float, float] | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Rows inside `x_range` (inclusive; all rows if None) that hold at least one value."""
    keep = np.ones(len(time), dtype=bool)
    if x_range is not None:
        keep &= (time >= x_range[0]) & (time <= x_range[1])
    if columns:
        keep &= ~np.all(np.isnan(np.column_stack(list(columns.values()))), axis=1)
    return time[keep], {name: values[keep] for name, values in columns.items()}


def export_table(
    path: str | Path,
    time: np.ndarray,
    columns: dict[str, np.ndarray],
    x_range: tuple[float, float] | None = None,
) -> int:
    """Writes the table in the format of `path`'s suffix; returns the number of rows."""
    path = Path(path)
    fmt = path.suffix.lower().lstrip(".")
    if fmt not in FORMATS:
        raise ExportError(f"unknown export format '{path.suffix}' (use .csv or .parquet)")
    time, columns = select_rows(time, columns, x_range)
    names = ["time_s", *columns]
    data = [time, *columns.values()]
    try:
        if fmt == "csv":
            _write_csv(path, names, data)
        else:
            _write_parquet(path, names, data)
    except OSError as e:
        raise ExportError(f"cannot write {path}: {e}") from e
    return len(time)


def _write_csv(path: Path, names: list[str], data: list[np.ndarray]) -> None:
    buf = io.StringIO()
    # %.10g round-trips float32 telemetry and keeps integers integral.
    np.savetxt(buf, np.column_stack(data), fmt="%.10g", delimiter=",")
    body = buf.getvalue().replace("nan", "")  # a missing value is an empty field
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(names) + "\n")
        f.write(body)


def _write_parquet(path: Path, names: list[str], data: list[np.ndarray]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ExportError("Parquet export needs the optional pyarrow package") from e
    arrays = [pa.array(col, from_pandas=True) for col in data]  # NaN -> null
    pq.write_table(pa.table(arrays, names=names), path)
