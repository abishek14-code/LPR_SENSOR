# P3 simulation-only Python controller

Run from the repository root:

```text
pytest -q
python -m python.main --demo
python -m python.main --experiments --trials 100 --variants baseline conservative enhanced no_reverse --output-dir results
```

The fixed-seed demo needs no external files. Healthy releases without replay;
a moderately slow **same sample** passes replay and one longer-dwell retry;
a gain fault fails replay and rejects. Reset interrupts execution; timeout stops
at the configured point limit.

Recorded mode:

```text
python -m python.main --recorded-csv trace.csv --source-type ltspice --evidence-json checks.json
```

Use multisim as the source type for Multisim exports. Without actual matching
opening/closing/OCP evidence, measurement-only CSVs reject for release.
Recorded mode never performs automatic replay or reacquisition. Elmer is spatial-only.

See [controller documentation](../docs/python_controller.md) for equations, schema,
thresholds, replay policy, timing, fault definitions, paired trials and limitations.
Exact tested direct dependency versions are in [requirements-tested.txt](requirements-tested.txt).
Other environments require verification.

Generated results are ignored by Git. The full matrix requests 5,600 executions;
read the campaign manifest for the actual completed count and source revision.
Figure and trial manifests must refer to the clean code used for generation.
No physical validation, corrosion-rate accuracy or patent-novelty claim is made.

## Canonical decision integration

`AttemptRecord.decision_result` adds ACCEPT/REJECT/WAIT_OR_REPEAT with check
evidence, actions, recovery budgets and explicit estimate eligibility. The demo
shows WAIT_OR_REPEAT on the slow scenario's first attempt before the successful
bounded repeat. See [integration contract](../docs/p3_integration_contract.md)
for the new standalone evaluation APIs and future upstream requirements, and
[P3 validation report](../docs/P3_VALIDATION_REPORT.md) for actual executed checks.
Missing/malformed recorded-file input now emits a structured rejection and the
CLI exits with status 2; the legacy Python CSV API retains ValueError behavior.

## PHALK environmental-context extension

PHALK validation and optional context remain separate from circuit measurements,
physical inputs and controller decisions. See [PHALK contract](../docs/phalk_dataset_contract.md)
and the updated P3 validation report for actual data-quality findings, supported
interfaces and unverified physical dependencies. The original Python controller
and quality-policy code remain unchanged.
