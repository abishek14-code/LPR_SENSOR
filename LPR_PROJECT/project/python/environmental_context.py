"""Optional PHALK context: never an input to the circuit-quality policy."""
from dataclasses import dataclass, field, asdict
from typing import Optional
import math
import numpy as np
import pandas as pd

from .decision_engine import DecisionResult, evaluate_measurement
from .io_utils import json_safe
from .phalk_adapter import PHALKDataset, PHALKValidationConfig, LITHOLOGY_COLUMNS, validate_phalk_frame


def _observed_number(value):
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def _observed_identifier(value):
    value = _observed_number(value)
    return int(value) if value is not None and value > 0 and value % 1 == 0 else None


@dataclass(frozen=True)
class EnvironmentalContext:
    available: bool = False
    validated: bool = False
    validation_status: str = "UNAVAILABLE"
    observed_alkalinity_ueq_l: Optional[float] = None
    alkalinity_group: Optional[str] = None
    site_provenance: dict = field(default_factory=dict)
    geographic_identifiers: dict = field(default_factory=dict)
    environmental_predictors: dict = field(default_factory=dict)
    field_roles: dict = field(default_factory=dict)
    validation_checks: tuple = ()
    limitations: tuple = ("Optional environmental metadata unavailable; no physical calculation currently requires it.",)

    def to_dict(self):
        return json_safe(asdict(self))

    def as_physical_inputs(self):
        raise ValueError("Unsupported PHALK mapping: alkalinity is not conductivity; watershed predictors are not electrode geometry/steel area; context supplies no Ru, Rp, voltage/current/time traces or corrosion-rate reference.")


def context_from_phalk(dataset: PHALKDataset, phalk_id):
    """Select by actual record ID without first-match resolution or filtering."""
    if not isinstance(dataset, PHALKDataset):
        raise ValueError("dataset must be a PHALKDataset")
    if "PHALK_ID" not in dataset.data:
        return EnvironmentalContext(validation_status="INVALID_DATASET_SCHEMA", limitations=("The dataset lacks PHALK_ID; optional context is unavailable.",))
    requested_id = _observed_identifier(phalk_id)
    if requested_id is None:
        return EnvironmentalContext(validation_status="INVALID_RECORD_ID", limitations=("Select a positive integer PHALK_ID.",))
    matches = pd.to_numeric(dataset.data.PHALK_ID, errors="coerce").eq(requested_id)
    positions = [index for index, matched in enumerate(matches) if matched]
    if len(positions) != 1:
        return EnvironmentalContext(validation_status="UNAVAILABLE" if not positions else "AMBIGUOUS_RECORD",
            limitations=("PHALK_ID was not found or was not unique; no record is silently selected.",))
    position = positions[0]
    row = dataset.data.iloc[position]
    report = validate_phalk_frame(dataset.data.iloc[[position]], PHALKValidationConfig())
    valid = report.validated and position + 1 not in dataset.report.invalid_record_numbers
    # Retain known finite observations even when another field fails validation.
    # Unknown/nonfinite values stay None, and the explicit validation flag stays false.
    predictors = {name: {"value": _observed_number(row.get(name)), "units": "percent of upstream watershed", "role": "contextual", "required_by_current_physical_model": False} for name in LITHOLOGY_COLUMNS}
    predictors["slp_dg_uav"] = {"value": _observed_number(row.get("slp_dg_uav")), "units": "HydroATLAS encoded degrees x10", "role": "contextual", "required_by_current_physical_model": False, "encoding_independently_verified_for_PHALK_Greenland": False}
    return EnvironmentalContext(True, valid, "VALIDATED_CONTEXT_FIELDS" if valid else report.status if not report.validated else "DATASET_INTEGRITY_UNVERIFIED",
        _observed_number(row.get("value")), row.get("Alkalinity_gr") if isinstance(row.get("Alkalinity_gr"), str) else None,
        {"latitude": _observed_number(row.get("site_latitude")), "longitude": _observed_number(row.get("site_longitude")), "coordinate_units": "degrees", "crs": "WGS84",
         "site_source": row.get("site_source") if isinstance(row.get("site_source"), str) else None,
         "site_reference": row.get("site_reference") if isinstance(row.get("site_reference"), str) else None, "source_artifact": dataset.report.source,
         "bibliography_resolution": "UNVERIFIED"},
        {"HYBAS_ID": _observed_identifier(row.get("HYBAS_ID")), "PHALK_ID": _observed_identifier(row.get("PHALK_ID"))}, predictors,
        {name: {"role": "contextual", "required_by_current_physical_model": False} for name in ("observed_alkalinity_ueq_l", "site_provenance", "geographic_identifiers", "environmental_predictors")},
        tuple(asdict(check) for check in report.checks if check.status not in {"PASS", "SKIPPED"}), report.limitations)


@dataclass(frozen=True)
class ContextualDecisionResult:
    decision_result: DecisionResult
    environmental_context: EnvironmentalContext

    @property
    def decision(self):
        return self.decision_result.decision

    def to_dict(self):
        return {"decision_result": self.decision_result.to_dict(), "environmental_context": self.environmental_context.to_dict()}


def evaluate_with_environment(frames, config=None, *, environmental_context=None, **measurement_kwargs):
    """Attach optional context after evaluation; never merge it into physical inputs."""
    decision = evaluate_measurement(frames, config, **measurement_kwargs)
    if environmental_context is None:
        environmental_context = EnvironmentalContext()
    elif not isinstance(environmental_context, EnvironmentalContext):
        environmental_context = EnvironmentalContext(validation_status="INVALID_METADATA_TYPE", limitations=("Optional context must be EnvironmentalContext; the circuit decision is unaffected.",))
    return ContextualDecisionResult(decision, environmental_context)
