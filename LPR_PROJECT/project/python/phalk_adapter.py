"""PHALK observed alkalinity validation; separate from circuit/physical inputs.

Source: Batalla, Martinez-Artero & Catalan (2026), Scientific Data 13:686,
doi:10.1038/s41597-026-07028-2, pp.3-6,12-13 and Table 4.
Negative alkalinity is meaningful. The published log uses 0.1 only as the
nonpositive-value modeling proxy; original observations are never rewritten.
"""
from dataclasses import dataclass, field, asdict
from io import BytesIO
from numbers import Real
from pathlib import Path
from typing import Optional
import argparse
import hashlib
import json

import numpy as np
import pandas as pd

from .io_utils import json_safe
from .validation import finite_number, integer

LITHOLOGY_COLUMNS = (
    "XX_mt_U", "XX_sc_U", "XX_ss_U", "XX_su_U", "XX_sm_U", "XX_pa_U", "XX_pb_U",
    "XX_vb_U", "XX_pi_U", "XX_va_U", "XX_vi_U", "XX_ev_U", "XX_ig_U", "XX_wb_U", "XX_py_U",
)
PHALK_COLUMNS = (
    "HYBAS_ID", "PHALK_ID", "site_latitude", "site_longitude", "site_source", "site_reference",
    "value_log10", "value", "Alkalinity_gr", *LITHOLOGY_COLUMNS, "slp_dg_uav",
)
NUMERIC_COLUMNS = ("HYBAS_ID", "PHALK_ID", "site_latitude", "site_longitude", "value_log10", "value", *LITHOLOGY_COLUMNS, "slp_dg_uav")
GROUPS = (
    "a) < 200", "b) 200-500", "c) 500-1000", "d) 1000-2000",
    "e) 2000-3000", "f) 3000-4000", "g) >4000",
)
KNOWN_SOURCES = frozenset(("GLORICH", "GEMSTAT", "WATERBASE", "CESI", "WQP", "ESTONIA", "MYVATN", "TSADB", "NADUF", "CDD", "MONGOLIA", "CWSM", "AKROTIRI", "ARCTIC", "BEL&UKR", "GEM", "NSLS", "OTHER DATA", "LITERATURE"))
SUPPLIED_CSV_SHA256 = "2dce7c0a98bd6d661857dd0fa6e45933b5674e7dde906428ffb5e2a92547c3a7"
PAPER_SHA256 = "82bf21f9c1b9ca3ed2f54a593f1e9933b33a1fd95fa0f8cb81fc682774eb7fa5"
PAPER_DOI = "https://doi.org/10.1038/s41597-026-07028-2"
DATASET_DOI = "https://doi.org/10.5281/zenodo.15591825"
SLOPE_REFERENCE = "https://data.hydrosheds.org/file/technical-documentation/BasinATLAS_Catalog_v10.pdf"


@dataclass(frozen=True)
class PHALKValidationConfig:
    expected_row_count: Optional[int] = None
    expected_sha256: Optional[str] = None
    log10_atol: float = 1e-12
    max_examples: int = 8

    def __post_init__(self):
        if self.expected_row_count is not None:
            integer(self.expected_row_count, "expected_row_count", minimum=1)
        integer(self.max_examples, "max_examples", minimum=1)
        finite_number(self.log10_atol, "log10_atol", positive=True)
        if self.expected_sha256 is not None and (not isinstance(self.expected_sha256, str) or len(self.expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.expected_sha256)):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 hexadecimal digest")


SUPPLIED_ARTIFACT = PHALKValidationConfig(expected_row_count=50916, expected_sha256=SUPPLIED_CSV_SHA256)


@dataclass(frozen=True)
class DatasetCheck:
    code: str
    status: str
    affected_count: int
    expected: object
    examples: tuple = ()
    explanation: str = ""


@dataclass(frozen=True)
class PHALKValidationReport:
    status: str
    available: bool
    validated: bool
    row_count: int
    column_count: int
    columns: tuple
    source: dict
    checks: tuple
    invalid_record_numbers: tuple
    statistics: dict
    limitations: tuple
    filtered_records: int = 0

    def to_dict(self):
        return json_safe(asdict(self))


@dataclass
class PHALKDataset:
    data: pd.DataFrame
    report: PHALKValidationReport


def category_memberships(value):
    """Table 4 in meq/L converted to CSV ueq/L. Interior endpoints overlap.

    Paper ranges use hyphens without a tie rule. Accept either adjacent band
    at interior endpoints, and report that ambiguity. Outer <200 and >4000
    are explicit strict inequalities; the artifact's five a)/200 rows fail.
    """
    finite_number(value, "alkalinity")
    intervals = ((-np.inf, 200), (200, 500), (500, 1000), (1000, 2000), (2000, 3000), (3000, 4000), (4000, np.inf))
    return tuple(label for i, (label, (low, high)) in enumerate(zip(GROUPS, intervals))
                 if (value < high if i == 0 else value > low if i == 6 else low <= value <= high))


def _numeric(series):
    missing = series.isna() | series.map(lambda v: isinstance(v, str) and not v.strip())
    invalid_type = series.map(lambda v: isinstance(v, (bool, np.bool_)) or (not isinstance(v, (Real, str)) and v is not None)) & ~missing
    cleaned = series.astype(object).where(~invalid_type, np.nan)
    numeric = pd.to_numeric(cleaned, errors="coerce").to_numpy(dtype=float)
    literal_nonfinite = series.map(lambda v: isinstance(v, str) and v.strip().lower() in {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"})
    nonfinite = (~np.isfinite(numeric) & ~np.isnan(numeric)) | literal_nonfinite.to_numpy()
    malformed = np.isnan(numeric) & ~missing.to_numpy() & ~literal_nonfinite.to_numpy()
    return numeric, {"MISSING_VALUE": missing.to_numpy(), "INVALID_NUMERIC_TYPE": invalid_type.to_numpy(),
                     "NONFINITE_VALUE": nonfinite, "MALFORMED_NUMERIC": malformed & ~invalid_type.to_numpy()}


def _whole_numbers(values):
    finite = np.isfinite(values)
    whole = np.zeros(len(values), dtype=bool)
    whole[finite] = values[finite] % 1 == 0
    return whole


def validate_phalk_frame(frame, config=None, *, source_path=None, source_sha256=None):
    """Read-only validation: conversion occurs only in a derived numeric view."""
    config = PHALKValidationConfig() if config is None else config
    if not isinstance(config, PHALKValidationConfig):
        raise ValueError("config must be PHALKValidationConfig")
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("PHALK input must be a DataFrame")
    data = frame.reset_index(drop=True)
    checks, invalid = [], set()
    n = len(data)

    def add(code, mask, expected, explanation, *, severity="ERROR", fields=()):
        mask = np.asarray(mask, dtype=bool)
        positions = np.flatnonzero(mask)
        if severity == "ERROR":
            invalid.update(int(i) + 1 for i in positions)
        examples = tuple({"record_number": int(i)+1, **{name: {"value": json_safe(data.iloc[i][name]), "representation": repr(data.iloc[i][name])} for name in fields if name in data}}
                         for i in positions[:config.max_examples])
        checks.append(DatasetCheck(code, "PASS" if not len(positions) else "FAIL" if severity == "ERROR" else severity,
                                   len(positions), expected, examples, explanation))

    missing_columns = sorted(set(PHALK_COLUMNS) - set(data))
    unexpected_columns = sorted(set(data) - set(PHALK_COLUMNS))
    duplicated_columns = list(data.columns[data.columns.duplicated()])
    schema_valid = not missing_columns and not unexpected_columns and not duplicated_columns
    checks.append(DatasetCheck("SCHEMA", "PASS" if schema_valid else "FAIL", len(missing_columns)+len(unexpected_columns)+len(duplicated_columns),
        list(PHALK_COLUMNS), ({"missing": missing_columns, "unexpected": unexpected_columns, "duplicate_columns": duplicated_columns},),
        "Exact supplied 25-column schema; no additional physical measurement columns are inferred."))
    count_valid = n > 0 and (config.expected_row_count is None or n == config.expected_row_count)
    checks.append(DatasetCheck("ROW_COUNT", "PASS" if count_valid else "FAIL", 0 if count_valid else n,
                              config.expected_row_count or "at least one record", ({"observed": n},)))
    hash_valid = config.expected_sha256 is None or source_sha256 == config.expected_sha256
    checks.append(DatasetCheck("ARTIFACT_SHA256", "SKIPPED" if config.expected_sha256 is None else "PASS" if hash_valid else "FAIL",
        0 if hash_valid else n, config.expected_sha256, ({"observed": source_sha256},), "The digest binds the exact parsed bytes, not laboratory quality."))
    source = {"path": str(source_path) if source_path is not None else None, "sha256": source_sha256,
              "paper_doi": PAPER_DOI, "paper_sha256": PAPER_SHA256, "dataset_doi": DATASET_DOI}
    limitations = (
        "Site-level summarized alkalinity, not a temporal circuit measurement or corrosion-rate reference.",
        "Ionic-balance and laboratory quality cannot be independently reproduced from these 25 fields.",
        "Reference text is present but bibliography resolution is unverified: references_SurfaceWaters.csv was not supplied.",
        "Basin membership cannot be independently checked without HydroBASINS polygons.",
        "Interior category tie rules are unspecified in Table 4; either adjacent interval is allowed at shared endpoints.",
        "slp_dg_uav is preserved in HydroATLAS encoded degrees x10; PHALK's Greenland replacement encoding is not independently verified without its field catalog.",
        "No automatic filtering, record deletion, unit rewriting, or physical-input conversion is performed.",
    )
    if not schema_valid or not count_valid or not hash_valid:
        invalid.update(range(1, n+1))
    if not schema_valid:
        return PHALKValidationReport("INVALID_SCHEMA", True, False, n, len(data.columns), tuple(data.columns), source,
                                     tuple(checks), tuple(sorted(invalid)), {}, limitations)

    numbers = {}
    for name in NUMERIC_COLUMNS:
        numeric, problems = _numeric(data[name])
        numbers[name] = numeric
        for code, mask in problems.items():
            add(code + "." + name, mask, "finite real number; integer identifier/encoded slope where specified", "Invalid values are reported without modifying source cells.", fields=(name, "PHALK_ID"))
    for name in ("site_source", "site_reference", "Alkalinity_gr"):
        malformed = data[name].map(lambda v: not isinstance(v, str) or not v.strip())
        add("MISSING_OR_INVALID_TEXT." + name, malformed, "nonempty source text", "Required provenance/category text cannot be fabricated.", fields=(name, "PHALK_ID"))
    for name in ("HYBAS_ID", "PHALK_ID"):
        values = numbers[name]
        bad = np.isfinite(values) & ((values <= 0) | ~_whole_numbers(values) | (values > 2**53 - 1))
        if name == "HYBAS_ID":
            bad |= np.isfinite(values) & ((values < 1_000_000_000) | (values > 9_999_999_999))
        add("IDENTIFIER_INTEGRITY." + name, bad, "positive exact integer; supplied HYBAS identifiers contain 10 digits", "Identifier integrity is distinct from geographic basin membership.", fields=(name,))
    for name, low, high in (("site_latitude", -90, 90), ("site_longitude", -180, 180)):
        values = numbers[name]
        add("COORDINATE_RANGE." + name, np.isfinite(values) & ((values < low) | (values > high)), {"minimum": low, "maximum": high, "crs": "WGS84"}, "Paper p.3 documents WGS84 coordinates.", fields=(name, "PHALK_ID"))
    duplicate_rows = data.duplicated(keep=False).to_numpy()
    add("EXACT_DUPLICATE_RECORD", duplicate_rows, "no exact repeated record", "Report duplicates; never delete automatically.", fields=("PHALK_ID", "HYBAS_ID", "value"))
    valid_id = _whole_numbers(numbers["PHALK_ID"]) & (numbers["PHALK_ID"] > 0)
    id_series = pd.Series(numbers["PHALK_ID"])
    duplicate_ids = valid_id & id_series.duplicated(keep=False).to_numpy()
    add("DUPLICATE_PHALK_ID", duplicate_ids, "unique PHALK record identifier in this artifact", "HYBAS_ID repetition is expected; PHALK_ID repetition must be examined.", fields=("PHALK_ID", "value", "site_latitude", "site_longitude"))
    conflicting_ids = np.zeros(n, dtype=bool)
    for _, group in data.loc[duplicate_ids].assign(_canonical_id=numbers["PHALK_ID"][duplicate_ids]).groupby("_canonical_id", sort=False):
        if len(group.drop(columns="_canonical_id").drop_duplicates()) > 1:
            conflicting_ids[group.index.to_numpy()] = True
    add("CONFLICTING_PHALK_ID", conflicting_ids, "one consistent record per PHALK_ID", "Conflicting repeated identifiers are not resolved by first-match selection.", fields=("PHALK_ID", "value", "site_source"))
    source_known = data.site_source.map(lambda v: isinstance(v, str) and v.strip().upper() in KNOWN_SOURCES)
    add("UNRECOGNIZED_SOURCE", ~source_known.to_numpy(), "a source listed in paper Table 1", "An unfamiliar source needs provenance review; it is not silently relabelled.", severity="WARN", fields=("site_source", "site_reference"))
    values, logs = numbers["value"], numbers["value_log10"]
    comparable = np.isfinite(values) & np.isfinite(logs)
    positive = np.isfinite(values) & (values > 0)
    expected_logs = np.full(n, -1.0)
    expected_logs[positive] = np.log10(values[positive])
    residual = np.abs(logs - expected_logs)
    add("LOG10_CONSISTENCY", comparable & (residual > config.log10_atol),
        {"positive": "log10(value in ueq/L)", "nonpositive": -1.0, "absolute_tolerance": config.log10_atol},
        "Paper p.6 encodes zero/negative values as 0.1 for modeling; observed value remains unchanged.", fields=("PHALK_ID", "value", "value_log10"))
    group_match = np.zeros(n, dtype=bool)
    interior_boundary = np.isfinite(values) & np.isin(values, (500, 1000, 2000, 3000))
    for i, value in enumerate(values):
        label = data.Alkalinity_gr.iloc[i]
        if np.isfinite(value) and isinstance(label, str):
            group_match[i] = label in category_memberships(float(value))
    add("ALKALINITY_CATEGORY", np.isfinite(values) & ~group_match, {"bands_ueq_l": list(GROUPS)},
        "Table 4 uses meq/L; multiply thresholds by 1000. The explicit <200 bound excludes 200.", fields=("PHALK_ID", "value", "Alkalinity_gr"))
    add("INTERIOR_CATEGORY_BOUNDARY", interior_boundary, "either adjacent band is permitted where Table 4 gives no tie rule", "Ambiguous interior endpoints are reported without inventing a unique category.", severity="INFO", fields=("PHALK_ID", "value", "Alkalinity_gr"))
    for name in LITHOLOGY_COLUMNS:
        vals = numbers[name]
        add("LITHOLOGY_PERCENTAGE." + name, np.isfinite(vals) & ((vals < 0) | (vals > 100)), {"minimum_percent": 0, "maximum_percent": 100}, "Paper Table 3 specifies upstream watershed lithology percentages.", fields=(name, "PHALK_ID"))
    slope = numbers["slp_dg_uav"]
    add("SLOPE_ENCODING", np.isfinite(slope) & ((slope < 0) | (slope > 900) | ~_whole_numbers(slope)),
        {"minimum": 0, "maximum": 900, "type": "integer", "encoding": "HydroATLAS degrees x10", "reference": SLOPE_REFERENCE},
        "NoData sentinels and unsupported encoding are flagged; raw encoded values remain contextual.", fields=("slp_dg_uav", "PHALK_ID"))
    total = np.column_stack([numbers[name] for name in LITHOLOGY_COLUMNS]).sum(axis=1)
    predictor_view = pd.DataFrame({name: numbers[name] for name in (*LITHOLOGY_COLUMNS, "slp_dg_uav")})
    predictor_view["_basin"] = numbers["HYBAS_ID"]
    variation = predictor_view.groupby("_basin").nunique(dropna=True).gt(1).any(axis=1)
    conflicting_basins = variation.index[variation]
    add("PREDICTOR_CONSISTENCY_BY_BASIN", pd.Series(numbers["HYBAS_ID"]).isin(conflicting_basins).to_numpy(),
        "upstream predictor values agree for the same basin identifier", "Review conflicting watershed descriptions; no predictor is silently selected or rewritten.",
        severity="WARN", fields=("HYBAS_ID", "PHALK_ID"))
    nonclosure = np.isfinite(total) & (np.abs(total - 100) > 1e-9)
    add("LITHOLOGY_SUM_NOT_100", nonclosure, "review coverage and stored rounding; no mandatory 100-percent closure imposed",
        "Only 15 lithology fields are supplied; the paper describes 16 GLiM classes. Closure cannot be enforced without complete coverage metadata.", severity="WARN", fields=("HYBAS_ID", "PHALK_ID"))
    candidate = data.duplicated(("site_latitude", "site_longitude", "value", "site_source", "site_reference"), keep=False).to_numpy() & ~duplicate_rows
    add("POTENTIAL_RELATED_SITE_RECORD", candidate, "review original site/water-type metadata before any deduplication",
        "Paper p.4 retains distinct water types at nearby sites; this export lacks the water-type information needed to resolve candidates.", severity="WARN", fields=("PHALK_ID", "HYBAS_ID", "site_latitude", "site_longitude", "value"))
    stats = {
        "negative_alkalinity_records": int((np.isfinite(values) & (values < 0)).sum()),
        "zero_alkalinity_records": int((np.isfinite(values) & (values == 0)).sum()),
        "positive_log10_max_absolute_error": float(residual[positive & np.isfinite(logs)].max()) if (positive & np.isfinite(logs)).any() else None,
        "unique_hybas_ids": int(data.HYBAS_ID.nunique()), "unique_phalk_ids": int(data.PHALK_ID.nunique()),
        "duplicate_extra_records": int(data.duplicated().sum()),
        "repeated_coordinate_extra_records": int(data.duplicated(("site_latitude", "site_longitude")).sum()),
        "potential_related_site_extra_records": int(data.duplicated(("site_latitude", "site_longitude", "value", "site_source", "site_reference")).sum()),
        "basins_with_conflicting_predictors": len(conflicting_basins),
        "lithology_sum_min_percent": float(np.min(total)) if len(total) and np.isfinite(total).all() else None,
        "lithology_sum_max_percent": float(np.max(total)) if len(total) and np.isfinite(total).all() else None,
        "lithology_sum_not_100_records": int(nonclosure.sum()),
        "lithology_sum_over_100_records": int((np.isfinite(total) & (total > 100 + 1e-9)).sum()),
        "source_counts": data.site_source.value_counts(dropna=False).to_dict(),
        "category_counts": data.Alkalinity_gr.value_counts(dropna=False).to_dict(),
        "numeric_ranges": {name: {"minimum": float(vals[np.isfinite(vals)].min()) if np.isfinite(vals).any() else None,
                                  "maximum": float(vals[np.isfinite(vals)].max()) if np.isfinite(vals).any() else None} for name, vals in numbers.items()},
    }
    failed = any(check.status == "FAIL" for check in checks)
    warned = any(check.status == "WARN" for check in checks)
    return PHALKValidationReport("ISSUES_FOUND" if failed else "VALIDATED_WITH_WARNINGS" if warned else "VALIDATED",
                                 True, not failed, n, len(data.columns), tuple(data.columns), source, tuple(checks), tuple(sorted(invalid)), stats, limitations)


def load_phalk_csv(path, config=None):
    """Load the supplied artifact by default; never rewrite or filter the file."""
    config = SUPPLIED_ARTIFACT if config is None else config
    path = Path(path)
    try:
        payload = path.read_bytes()
        header = pd.read_csv(BytesIO(payload), nrows=0).columns
        # pandas may rename duplicate headers; inspect literal headers as well.
        import csv
        literal_header = next(csv.reader(payload.decode("utf-8-sig").splitlines()))
        if len(literal_header) != len(set(literal_header)):
            raise ValueError("duplicate CSV header names")
        data = pd.read_csv(BytesIO(payload), dtype={name: "string" for name in ("HYBAS_ID", "PHALK_ID") if name in header}, keep_default_na=False)
    except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError, StopIteration) as exc:
        raise ValueError(f"could not load PHALK CSV {path}: {exc}") from exc
    report = validate_phalk_frame(data, config, source_path=path, source_sha256=hashlib.sha256(payload).hexdigest())
    return PHALKDataset(data, report)


def format_phalk_report(report):
    lines = ["# PHALK dataset validation", "", f"Status: {report.status}. Rows: {report.row_count:,}. Columns: {report.column_count}.",
             f"Source SHA-256: {report.source['sha256']}", "", "The original dataset was not rewritten, filtered, or used as circuit measurements.",
             "", "| Check | Status | Affected records |", "|---|---|---:|"]
    lines.extend(f"| {check.code} | {check.status} | {check.affected_count} |" for check in report.checks)
    for check in report.checks:
        if check.status not in {"PASS", "SKIPPED"}:
            lines.extend(["", f"## {check.code}", check.explanation, f"Expected: {check.expected}"])
            lines.extend("Observed: " + json.dumps(json_safe(example), sort_keys=True) for example in check.examples)
    lines.extend(["", "## Statistics", "```json", json.dumps(json_safe(report.statistics), indent=2, sort_keys=True), "```", "", "## Limitations"])
    lines.extend("- " + limitation for limitation in report.limitations)
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate PHALK environmental data separately from P3 circuit decisions")
    parser.add_argument("csv_path")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    dataset = load_phalk_csv(args.csv_path)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output/"phalk-validation.json").write_text(json.dumps(dataset.report.to_dict(), indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    (output/"phalk-validation.md").write_text(format_phalk_report(dataset.report), encoding="utf-8")
    print(f"{dataset.report.status}: {dataset.report.row_count} records, {dataset.report.column_count} fields; invalid records={len(dataset.report.invalid_record_numbers)}")
    return 0 if dataset.report.validated else 1


if __name__ == "__main__":
    raise SystemExit(main())
