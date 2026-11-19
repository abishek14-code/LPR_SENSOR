# LPR Sensor Simulation

An end-to-end simulation of a linear polarization resistance (LPR) corrosion sensor for steel in water. Python coordinates three stages:

1. **Elmer** (ElmerSolver) solves the electric field in the water and gives the uncompensated solution resistance **Ru** with an uncertainty.
2. **LTspice** runs the potentiostat circuit with a Randles cell model and records the measured voltage, current and instrument channels.
3. **The decision engine** checks settling, faults and uncertainty, then **accepts** (Rp plus a conditional corrosion rate with uncertainty), **rejects** (failure reasons, no number), or **asks for a repeat** with a longer observation, which loops back to LTspice.

A local 3D web page shows the cell, the Elmer field and the LTspice waveforms, and can start new runs.

```
shared inputs ─┬─> Elmer water field ──> Ru ± u ─────────────┐
(steel area,   │                                             ├─> decision engine ─> ACCEPT / REJECT / WAIT-REPEAT
 conductivity, └─> cell interface ─> LTspice circuit ─> V, I ┘                                        │
 geometry)                                 ^                                                          │
                                           └──────── repeat with a new voltage sequence and time ─────┘
```

## Repository layout

| Path | Contents |
|---|---|
| `LPR_PROJECT/code/flowchart_pipeline.py` | Runs the whole flowchart |
| `LPR_PROJECT/code/` | Elmer extraction (`p2_fem.py`, `mesh_refinement.py`), LTspice integration (`close_integration.py`, `p1_acquisition.py`, `interfaces.py`), closed-form check (`p4_reference.py`), tests |
| `LPR_PROJECT/project/` | Decision engine (Python package, hash-checked at start-up) |
| `LPR_PROJECT/configs/` | `shared_inputs.json` (edit this), `PARAMETER_SOURCES.json` (citation for every parameter), fixture and reference-resistor settings |
| `LPR_PROJECT/inputs/` | LTspice circuit deck and op-amp models (`P1`), Elmer mesh and solver file (`P2`) |
| `LPR_PROJECT/webapp/` | 3D simulator: Python server, page, and a local copy of three.js 0.160 (MIT) |
| `LPR_Unified_Circuit/` | The same circuit as an editable LTspice schematic, with 16 case presets |

## Prerequisites

Tested on macOS (Apple silicon).

- **Python 3.13** with the packages in `LPR_PROJECT/requirements.txt`.
- **ElmerSolver**, tested with Elmer 26.2: `brew install elmercsc/elmerfem/elmer`. Default path is `/opt/homebrew/bin/ElmerSolver`; set `LPR_ELMERSOLVER` to use another one.
- **LTspice for macOS**, tested with 17.2.4, from Analog Devices. Default path is `/Applications/LTspice.app/Contents/MacOS/LTspice`; set `LPR_LTSPICE_EXECUTABLE` to use another one.

Setup:

```sh
cd LPR_PROJECT
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x RUN_FLOWCHART_PIPELINE.command RUN_3D_SIMULATOR.command
```

## Run the simulation

From `LPR_PROJECT` (or double-click the `.command` files in Finder):

```sh
./RUN_FLOWCHART_PIPELINE.command                                    # all five decision branches, one after another
./RUN_FLOWCHART_PIPELINE.command --scenario gain_fault              # one scenario
./RUN_FLOWCHART_PIPELINE.command --conductivity 0.5 --gap 0.03 --reference-distance 0.015 --area 2e-6
./RUN_FLOWCHART_PIPELINE.command --all-branches --jobs 5 --overlap  # same results, run in parallel
```

Scenarios include `nominal_healthy`, `gain_fault`, `offset_fault`, `reverse_gain_fault`, `moderate_noise`, `high_noise`, `recoverable`, `persistent`, `timeout` and `reset`. A scenario injects a fault into the simulated cell or instrument; the decision engine never sees which scenario is running.

Each run writes `LPR_PROJECT/evidence/flowchart_runs/<time>_<scenario>/`. It contains `index.html` (the report), `PIPELINE_RESULT.json`, the Elmer solves, the LTspice decks, RAW files and `measured_channels.csv`, and `PARAMETER_AUDIT.json`. The terminal also prints the decision, Rp and corrosion rate.

Results on the development Mac, with the default inputs:

| Scenario | Path | Result |
|---|---|---|
| nominal_healthy | Accept | Ru 10000 ± 100 Ω, Rp 100001 ± 119 Ω, corrosion 0.151 ± 0.023 mm/yr |
| gain_fault | Reject | reference checks failed; no number |
| recoverable | Repeat (45 s/point) → Accept | Rp 100000 Ω |
| persistent | Repeat → Reject | retry limit exhausted |
| reset | Wait/repeat, no retry allowed | withheld |

## 3D simulator

```sh
./RUN_3D_SIMULATOR.command
```

It opens http://127.0.0.1:8765. Keep the Terminal window open while using the page. The page shows the steel working electrode, counter electrode, water and reference electrode in 3D. The Elmer potential field is a coloured slice with equipotential lines, and moving dots follow the Elmer current density. Both are scaled by the LTspice cell current at the playback time. Below the 3D view you can play or scrub the LTspice waveforms, and the side panels show the decision, Ru with its uncertainty budget, and the parameter audit. Set the scenario and sliders, then press **Run**: the server starts the same pipeline and the flowchart boxes turn green as each program finishes. The browser computes nothing physical. Elmer solves a 2D cross-section, so the depth is drawn larger than the model's 0.05 mm to keep the cell visible.

## Inputs and parameter sources

Edit `LPR_PROJECT/configs/shared_inputs.json`. It holds the steel area, water conductivity, electrode geometry, assumed cell parameters (Rp, Cdl per cm², Stern–Geary B) and the corrosion conversion. To use measured cell parameters, set `cell_interface.mode` to `calibrated` and point `calibration_file` at a JSON with `Rp_ohm`, `Cdl_F`, `stern_geary_B_V`, `stern_geary_B_standard_uncertainty_V` and `source`.

`LPR_PROJECT/configs/PARAMETER_SOURCES.json` gives every parameter a status and citation. A run stops if a parameter is missing from that file, and flags any value changed from it as `CHANGED_NEEDS_SOURCE`.

- **From the literature:** Stern–Geary relation and B = 26 mV for active steel (Stern & Geary 1957; Andrade & Alonso 2004); Cdl = 40 µF/cm² (Bard & Faulkner 2001); ±5/±10 mV steps and Rp = Rapp − Ru (ASTM G59; Mansfeld 1976); Randles cell (Randles 1947); corrosion conversion K, EW and density (ASTM G102).
- **Established concepts:** Ru = r/(σA) for a planar cell, checked by Elmer; GUM uncertainty propagation; mesh-refinement verification.
- **Assumed:** water conductivity 1 S/m, steel area 0.02 cm², 40 mm gap with the reference at mid-gap, Rp 100 kΩ, the B range, and 1 % tolerances.
- **Engineering settings:** the decision thresholds and circuit design values.

## Tests

```sh
cd LPR_PROJECT
PYTHONPATH=code:project .venv/bin/python -m pytest -q code/
```

The server tests skip until at least one run exists.

## Limits

This is a simulation of a declared planar fixture, not a qualified physical specimen. The corrosion rate is conditional on uniform corrosion, the declared B and the declared area. No hardware is controlled.

## Credits

Elmer water-field model: Danish. Decision engine: Darshan. LTspice circuit and integration: Abishek.
