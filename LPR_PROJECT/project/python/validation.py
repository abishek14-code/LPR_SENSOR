"""Shared, explicit validation for P3 analysis; no inferred physical limits."""
from numbers import Real, Integral
from collections.abc import Mapping

import numpy as np
import pandas as pd


def finite_number(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not np.isfinite(value):
        raise ValueError(f"{name} must be a finite real number (not a boolean)")
    if positive and value <= 0 or nonnegative and value < 0:
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return value


def integer(value, name, *, minimum=0):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum} (not a boolean)")
    return value


def boolean(value, name):
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")


def numeric_columns(frame, columns):
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("trace must be a pandas DataFrame")
    if frame.empty:
        raise ValueError("trace is empty")
    if frame.columns.duplicated().any():
        raise ValueError("ambiguous duplicate trace columns")
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"missing required trace columns: {sorted(missing)}")
    for name in columns:
        values = frame[name]
        if values.map(lambda v: isinstance(v, (bool, np.bool_))).any():
            raise ValueError(f"{name} contains boolean values instead of numeric measurements")
        if values.map(lambda v: v is not None and not isinstance(v, (Real, str))).any():
            raise ValueError(f"{name} contains unsupported measurement types; real numbers or numeric CSV text required")
        try:
            data = pd.to_numeric(values, errors="raise").to_numpy(dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} contains invalid numeric values: {exc}") from exc
        if not np.isfinite(data).all():
            index = int(np.flatnonzero(~np.isfinite(data))[0])
            raise ValueError(f"{name} contains nonfinite values (NaN or Inf) at row {frame.index[index]!r}: observed {values.iloc[index]!r}")


def validate_analysis_frame(frame):
    numeric_columns(frame, ("time_s", "actual_voltage_v", "current_a"))
    times = frame.time_s.to_numpy(dtype=float)
    if len(times) < 2 or np.any(times < 0) or np.any(np.diff(times) <= 0):
        raise ValueError("time_s must be nonnegative and strictly increasing with at least two samples")
    if "timed_out" in frame.attrs:
        boolean(frame.attrs["timed_out"], "timed_out")
    units = frame.attrs.get("units", {})
    if not isinstance(units, Mapping):
        raise ValueError("units must be a mapping")
    for name, expected in (("time_s", "s"), ("actual_voltage_v", "V"), ("current_a", "A")):
        if name in units and units[name] != expected:
            raise ValueError(f"{name}: expected canonical unit {expected}, received {units[name]}")
    if "point_index" in frame:
        try:
            numeric_columns(frame, ("point_index",))
        except ValueError as exc:
            raise ValueError(f"invalid point indices: {exc}") from exc
        ids = frame.point_index.to_numpy(dtype=float)
        if np.any(ids < 0) or np.any(ids % 1):
            raise ValueError("point_index must contain nonnegative integers")
        segments = frame.point_index[frame.point_index.ne(frame.point_index.shift())]
        if segments.duplicated().any():
            raise ValueError("point_index contains disjoint repeated windows")
    for name in ("sample_duration_s", "point_elapsed_time_s"):
        if name in frame:
            numeric_columns(frame, (name,))
            if (frame[name].astype(float) <= 0).any():
                raise ValueError(f"{name} must be positive")


def validate_ocp(frame):
    numeric_columns(frame, ("time_s", "ocp_voltage_v"))
    units = frame.attrs.get("units", {})
    if not isinstance(units, Mapping):
        raise ValueError("OCP units must be a mapping")
    for name, expected in (("time_s", "s"), ("ocp_voltage_v", "V")):
        if name in units and units[name] != expected:
            raise ValueError(f"OCP {name}: expected canonical unit {expected}, received {units[name]}")
    times = frame.time_s.to_numpy(dtype=float)
    if len(times) < 2 or np.any(times < 0) or np.any(np.diff(times) <= 0):
        raise ValueError("OCP time_s must be nonnegative and strictly increasing")
