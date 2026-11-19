# LPR Sensor Simulation

This repository contains the LPR sensor simulation software and its editable
LTspice circuit. The Python package evaluates sensor observations and produces
decision records; the circuit models the electrical measurement path.

## Repository layout

- `LPR_PROJECT/` — Python integration, supplied project source, configuration,
  model inputs, and source provenance.
- `LPR_Unified_Circuit/` — LTspice schematic, symbols, model libraries, test
  cases, and stimulus waveforms.

## Requirements

- Python 3.13 is the version used by the supplied package. Install dependencies
  from `LPR_PROJECT/requirements.txt`.
- LTspice is required to run the graphical circuit.
- A fresh full integrated simulation also requires the external native tools
  and runtime described in `LPR_PROJECT/MAC_RUN_GUIDE.md`. The launcher from
  the original computer-specific setup is not included.

## Run the Python decision workflow

From the repository root, create a virtual environment and install the Python
dependencies:

```sh
cd LPR_PROJECT
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the included synthetic controller scenario:

```sh
python run_integrated.py --mode synthetic --scenario healthy --no-open
```

This exercises the supplied controller using a synthetic scenario. It does not
run LTspice or Elmer and does not represent a physical sensor measurement. The
program writes a report and run record under its configured output paths.

## Run the LTspice circuit

Open `LPR_Unified_Circuit/LPR_Unified.asc` in LTspice and click **Run**. Keep
the full `LPR_Unified_Circuit/` directory together so its symbols, libraries,
stimuli, and case presets resolve. See
`LPR_Unified_Circuit/README.md` for traces and selecting cases.

## Scope and limitations

The included files support simulation and software decision evaluation. They do
not establish physical corrosion-rate measurement, sensor accuracy on a real
specimen, hardware validation, or operational safety. A Python `ACCEPT` result
is not physical qualification or release authorization.
