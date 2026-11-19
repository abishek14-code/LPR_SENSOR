> **Native Mac workflow:** See [MAC_RUN_GUIDE.md](MAC_RUN_GUIDE.md). Double-click `RUN_COMPLETE_PROJECT.command` for fresh Elmer → native LTspice → Python verification and the literature-dimensioned benchmark. The older instructions below describe the original delivery.

# LPR Project — organized delivery

Start with **START_HERE.html** or **decision_report.html**. The latter shows the
completed 16-case native simulation campaign and links to each actual decision.

## Run

On this Mac, double-click **RUN_SIMULATION.command** for a new native nominal
simulation. Add `--case recoverable` or `--matrix` in Terminal for other cases.
`--no-open` suppresses browser opening. Each execution uses a new run directory.

```sh
"/Users/rams/Documents/Codex/2026-09-30/files-mentioned-by-the-user-idp/outputs/LPR_PROJECT/RUN_SIMULATION.command"
```

Run **CHECK_PROJECT.command** to verify package hashes and run the 200 selected
regression/interface checks. This does not launch a new LTspice simulation.

## Folder guide

| Location | Contents |
|---|---|
| code/ | Integration, FEM extraction, reporting and interface tests |
| project/ | All 185 supplied scientific files, including unchanged P3 |
| configs/ | Geometry, material and versioned simulation-standard declarations |
| inputs/ | Electrical template/models and FEM geometry/mesh/solver sources |
| evidence/native/20261021T133828/ | Final 16 cases, 22 fresh native executions, all attempts/reference stages |
| evidence/P2/ | Five native Elmer solves, fields, logs and extraction results |
| evidence/verification_logs/ | Original verification logs and new organization checks |
| evidence/executed_sources/ | Exact source used for the final native matrix |
| docs/ | Implementation, limits and historical manuscript draft/figures |
| provenance/ | Original source identity, retained-file map and exclusions |

## Demonstrated scope

The qualified scope is the declared planar simulation fixture. Actual final
outcomes: 9 ACCEPT, 6 REJECT, 1 reset/incomplete WAIT_OR_REPEAT. Three actual
reacquisitions were executed. Nominal corrected Rp is 100000.58112871805 ohm.
P3 source and thresholds are unchanged. The separate 3D specimen remains
unqualified; no physical corrosion-rate result or hardware control is claimed.
The original source and native files have not been rewritten by this cleanup.

## What was excluded from this delivery

Caches, Finder metadata, earlier/development native campaigns, the unfinished
later run, old delivery ZIPs and duplicate packaging artifacts are excluded.
Scientific development failures are preserved in the workspace archive, together
with all previous deliveries. See provenance/ORGANIZATION_RECORD.json.
The new archive is a reorganized delivery, not a newly executed scientific campaign.

## Dependencies

The Mac launcher uses the bundled Python runtime and workspace work/python_deps.
Installed LTspice 26.0.2 and the existing Wine runtime at
work/ltspice_cli_20261004/host_runtime/Bottles/ltspice are required for fresh runs.
Those paths remain in place. Native solves may require normal Mac Terminal when
a sandbox blocks Wine process sockets. Fresh FEM solves additionally require
ElmerSolver, ElmerGrid and Gmsh. requirements.txt records the tested Python versions.
This is runnable on the existing Mac; it is not a self-contained simulator installer.

## Historical material

docs/historical_manuscript contains the older paper draft unchanged. Its numerical
statements may predate this final campaign. Current evidence is decision_report.html,
VALIDATION_SUMMARY.json and docs/IMPLEMENTATION_AND_VALIDATION.md.

The files run_integrated.py and decision_html.py remain at the project root because
the native integration imports them. project/ltspice and project/results are retained
as supplied scientific provenance, not disposable duplicates.
