"""Generic LTspice/Multisim trace normalization."""

from dataclasses import dataclass, field
from pathlib import Path
from io import BytesIO
import hashlib
from typing import Dict, Optional

import numpy as np
import pandas as pd
from .validation import finite_number, numeric_columns


@dataclass(frozen=True)
class TraceAdapterConfig:
    source_type: str = "ltspice"
    column_map: Dict[str, str] = field(default_factory=dict)
    voltage_scale: float = 1.0
    current_scale: float = 1.0
    time_scale: float = 1.0

    def __post_init__(self):
        if self.source_type not in {"ltspice", "multisim", "synthetic", "recorded"}:
            raise ValueError("unsupported adapter source_type")
        if not isinstance(self.column_map, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.column_map.items()):
            raise ValueError("column_map must map column names to column names")
        for name in ("voltage_scale", "current_scale", "time_scale"):
            finite_number(getattr(self, name), name, positive=True)
        if not all(np.isfinite(x) and x > 0 for x in (self.voltage_scale, self.current_scale, self.time_scale)):
            raise ValueError("adapter unit scales must be finite and positive")


REQUIRED = ("time_s", "actual_voltage_v", "current_a")


def normalize_trace(frame: pd.DataFrame, config: Optional[TraceAdapterConfig] = None) -> pd.DataFrame:
    config = TraceAdapterConfig() if config is None else config
    if not isinstance(config, TraceAdapterConfig):
        raise ValueError("config must be a TraceAdapterConfig")
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("simulator trace must be a pandas DataFrame")
    if frame.empty:
        raise ValueError("simulator trace is empty")
    renamed = frame.rename(columns=dict(config.column_map)).copy()
    aliases = {
        "voltage_v": "actual_voltage_v", "voltage": "actual_voltage_v",
        "current": "current_a", "time": "time_s",
        "requested_voltage": "requested_voltage_v", "actual_voltage": "actual_voltage_v",
        "commanded_voltage_v": "requested_voltage_v",
    }
    renamed = renamed.rename(columns={key: value for key, value in aliases.items() if key in renamed.columns})
    declared_units = frame.attrs.get("units", {})
    if not isinstance(declared_units, dict):
        raise ValueError("units must be a dictionary")
    conversions = {"time_s": ({"s": 1.0, "ms": .001, "us": 1e-6}, config.time_scale, "s"),
                   "actual_voltage_v": ({"V": 1.0, "mV": .001, "uV": 1e-6}, config.voltage_scale, "V"),
                   "current_a": ({"A": 1.0, "mA": .001, "uA": 1e-6, "nA": 1e-9}, config.current_scale, "A")}
    canonical_units = {}
    for original, unit in declared_units.items():
        name = aliases.get(config.column_map.get(original, original), config.column_map.get(original, original))
        if name in conversions:
            supported, selected, canonical = conversions[name]
            if not isinstance(unit, str) or unit not in supported or selected != supported[unit]:
                raise ValueError(f"{name}: declared unit {unit} conflicts with configured conversion scale {selected}")
            canonical_units[name] = canonical
    renamed.attrs["units"] = canonical_units
    renamed.attrs["source_units"] = dict(declared_units)
    if renamed.columns.duplicated().any():
        raise ValueError("ambiguous duplicate simulator column aliases")
    missing = set(REQUIRED) - set(renamed.columns)
    if missing:
        raise ValueError(f"missing required simulator columns: {sorted(missing)}")
    numeric_columns(renamed, REQUIRED)
    for column in REQUIRED:
        renamed[column] = pd.to_numeric(renamed[column], errors="raise")
    renamed["time_s"] *= config.time_scale
    renamed["actual_voltage_v"] *= config.voltage_scale
    renamed["current_a"] *= config.current_scale
    for column in ("requested_voltage_v", "adc_range_v"):
        if column in renamed.columns:
            numeric_columns(renamed, (column,))
            renamed[column] = pd.to_numeric(renamed[column], errors="raise") * config.voltage_scale
    if "adc_rate_sps" in renamed.columns:
        renamed["adc_rate_sps"] = pd.to_numeric(renamed["adc_rate_sps"], errors="raise")
    for column in ("gain", "adc_range_v", "adc_rate_sps", "requested_voltage_v", "seed", "point_index", "sample_duration_s"):
        if column not in renamed:
            continue
        numeric_columns(renamed, (column,))
        values = pd.to_numeric(renamed[column], errors="raise")
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"nonfinite operational metadata: {column}")
        if column in {"gain", "adc_range_v", "adc_rate_sps", "sample_duration_s"} and (values <= 0).any():
            raise ValueError(f"operational metadata must be positive: {column}")
        if column in {"seed", "point_index"} and ((values < 0).any() or (values % 1 != 0).any()):
            raise ValueError(f"operational metadata must be a nonnegative integer: {column}")
        renamed[column] = values
    if "sample_duration_s" in renamed:
        renamed["sample_duration_s"] *= config.time_scale
    for column in ("reverse", "drive_enabled"):
        if column in renamed:
            mapping = {"true": True, "false": False, "1": True, "0": False}
            values = renamed[column].astype(str).str.lower().map(mapping)
            if values.isna().any():
                raise ValueError(f"invalid boolean operational metadata: {column}")
            renamed[column] = values.astype(bool)
    for column in ("run_id", "trial_id", "attempt_id", "seed", "source_type"):
        if column in renamed:
            if renamed[column].isna().any() or renamed[column].astype(str).str.strip().eq("").any() or renamed[column].nunique() != 1:
                raise ValueError(f"mixed or missing identity boundary: {column}")
    if "source_type" in renamed and renamed["source_type"].iloc[0] != config.source_type:
        raise ValueError("source_type does not match selected adapter")
    if not np.isfinite(renamed[list(REQUIRED)].to_numpy(dtype=float)).all():
        raise ValueError("simulator trace contains NaN or Inf")
    times = renamed["time_s"].to_numpy(dtype=float)
    if len(times) < 2:
        raise ValueError("simulator trace must contain at least two samples")
    if np.any(times < 0) or np.any(np.diff(times) <= 0):
        raise ValueError("time_s must be strictly increasing")
    renamed.attrs["source_type"] = config.source_type
    renamed.attrs["adapter_config"] = {
        "source_type": config.source_type,
        "voltage_scale": config.voltage_scale,
        "current_scale": config.current_scale,
        "time_scale": config.time_scale,
    }
    return renamed


def load_simulator_csv(path, config: Optional[TraceAdapterConfig] = None) -> pd.DataFrame:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"simulator CSV does not exist: {path}")
    try:
        source_bytes = path.read_bytes()
        frame = pd.read_csv(BytesIO(source_bytes), dtype={key: str for key in ("run_id", "trial_id", "attempt_id")})
    except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        raise ValueError(f"could not read simulator CSV {path}: {exc}") from exc
    normalized = normalize_trace(frame, config)
    normalized.attrs["source_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    return normalized


def split_measurement_windows(frame: pd.DataFrame, *, reverse: bool = False) -> Dict[str, pd.DataFrame]:
    if "block_id" not in frame.columns:
        raise ValueError("recorded LPR pipeline requires a block_id column for A1/B/A2 windows")
    windows = {}
    labels = frame["block_id"].astype(str).str.upper()
    directions = frame["reverse"] if "reverse" in frame else labels.str.endswith("_REVERSE")
    if ((labels.str.endswith("_REVERSE")) & ~directions).any():
        raise ValueError("block label conflicts with reverse metadata")
    keys = labels.str.removesuffix("_REVERSE") + directions.map({True: ":reverse", False: ":forward"})
    segments = keys[keys.ne(keys.shift())]
    if segments.duplicated().any():
        raise ValueError("disjoint repeated measurement windows cross acquisition boundaries")
    selected = frame.loc[directions == reverse]
    for name, group in selected.groupby("block_id", sort=False):
        key = str(name).upper().removesuffix("_REVERSE")
        if key in {"A1", "B", "A2"}:
            if key in windows:
                raise ValueError("duplicate measurement window")
            windows[key] = group.assign(reverse=reverse).reset_index(drop=True)
    missing = {"A1", "B", "A2"} - set(windows)
    if missing and not reverse:
        raise ValueError(f"recorded trace is missing measurement windows: {sorted(missing)}")
    return windows
