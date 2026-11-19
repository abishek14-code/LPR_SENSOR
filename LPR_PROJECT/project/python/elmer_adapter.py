"""Boundary adapter for Elmer spatial-physics outputs.

Elmer data is intentionally not converted into an LPR acquisition time series.
"""

from pathlib import Path

import numpy as np
import pandas as pd


SPATIAL_FIELDS = {"potential_v", "electric_field_v_per_m", "current_density_a_per_m2", "electrode_current_a"}


def load_elmer_summary(path) -> pd.DataFrame:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Elmer summary does not exist: {path}")
    frame = pd.read_csv(path)
    if frame.empty:
        raise ValueError("Elmer summary is empty")
    spatial = SPATIAL_FIELDS.intersection(frame.columns)
    if not spatial:
        raise ValueError(f"Elmer summary requires one spatial field: {sorted(SPATIAL_FIELDS)}")
    numeric = frame[list(spatial)].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError("Elmer summary contains NaN or Inf")
    frame.attrs["source_type"] = "elmer"
    frame.attrs["evidence_type"] = "spatial-physics"
    frame.attrs["lpr_time_series"] = False
    return frame


def elmer_evidence_boundary() -> dict:
    return {
        "source_type": "elmer",
        "evidence_type": "spatial-physics",
        "accepted_fields": sorted(SPATIAL_FIELDS),
        "converted_to_lpr_time_series": False,
        "limitation": "Elmer spatial outputs do not by themselves validate the LPR acquisition controller.",
    }
