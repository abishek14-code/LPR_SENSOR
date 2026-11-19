# Editable, fully wired LPR circuit

Open **LPR_Unified.asc** in LTspice. Keep this folder together: the schematic uses the included symbols, model libraries, and stimulus files.

## Run

1. Start LTspice and choose File → Open → `LPR_Unified.asc`.
2. Click **Run**. The default case is `nominal_healthy`; its simulated duration is 676 seconds (simulation runs faster than real time).
3. Click a wire to plot its voltage. Useful nodes are `req`, `actual_hold`, `adc`, and `i_meas`. Use the zoom tool to inspect individual sections.
4. `i_meas` and `i_true` are helper voltages whose numerical values represent current in amperes. They are not physical voltage outputs. `I(Vsense)` is the actual simulated sensed current.

## Change a test

Right-click a resistor, capacitor, source, or parameter directive to edit it. Parts using `{RP}`, `{RU}`, `{CDL}`, etc. take their values from the `.param` lines at the top.

| Parameter | Meaning | Nominal |
|---|---|---|
| RP | Polarization resistance | 100k ohm |
| RU | Uncompensated solution resistance | 10k ohm |
| CDL | Double-layer capacitance | 100u F |
| RCOUNTER | Counter-electrode path resistance | 10k ohm |
| RF | TIA feedback resistance | 1Meg ohm |
| GAIN | Measurement gain multiplier | 1 |
| OFFSET | Conditioning voltage offset | 0 V |
| NOISE | Multiplier of the supplied noise waveform | 0 |
| REVERSEGAIN | Extra gain in the reverse sweep | 1 |
| UNSTABLE | Enables the original state-dependent disturbance when positive | 0 |

OFFSET/RF and NOISE/RF give equivalent current offset and noise scaling in amperes. Use `Meg` for mega; SPICE `m` means milli. The cell is `RP` in parallel with `CDL`, in series with `RU`.

To load one of the **16 archived initial-attempt presets**:

1. Save any custom version under a separate name, then close `LPR_Unified.asc` in LTspice.
2. Double-click `SELECT_CASE.command`, choose the case number, and press Return. This needs Python 3.
3. Reopen `LPR_Unified.asc` and click Run.

Alternatively, from Terminal in this folder:

```sh
python3 select_case.py --list
python3 select_case.py nominal_healthy
```

The selector changes the parameters, stop time, protocol stimulus files, and deterministic noise waveform. It keeps the drawn topology. Custom protocol waveforms can be edited in `stimuli/`; each line contains time in seconds and voltage. Selecting another preset replaces those files.

## What is connected

The 5 V supply and midpoint buffer feed the potentiostat and transimpedance amplifier. The control amplifier drives the counter-electrode path, the reference buffer senses RE, and the working-electrode path passes through the current sensor and selectable cell/calibration branches. The TIA output enters gain/offset/noise conditioning, an RC filter, and clocked sample-and-hold. Separate voltage sampling measures WE minus RE.

All shared top-level electrical nodes are connected by drawn wires. Each net has only one label, used for identifying/probing it. Dots indicate junctions; crossings without dots are separate wires. Protocol/status sources share the common ground; some status outputs are telemetry rather than inputs to analog components.

The six rectangular behavioral blocks expose the original equations through explicit input pins. Their equations are in `behavioral_blocks.lib`; the four generic op-amp models are in `p1_opamp.lib`. These retain the original simulation abstractions, including idealized switches and sampling.

This schematic represents the electrical simulation from the archived integrated `acquisition.cir`. Elmer geometry/field solving and Python acceptance decisions, analysis, and automatic retries remain separate project stages. Editing this ASC does not automatically modify the original project runner's circuit template. Presets load attempt 0, not the full retry sequence.

The source deck and original project files were not overwritten by this circuit conversion. Builder scripts regenerate the schematic and overwrite layout edits; they are development tools, not part of the normal run workflow.

## Verification

`validation/verification.json` records the checks against the original source: all 48 top-level symbols, drawn-wire connectivity, native LTspice netlist connectivity, all 16 preset parameter/stimulus mappings, and nominal sampled-waveform comparison. The original and converted models can show operating-point convergence fallback messages before transient analysis; completion and sampled transient agreement are checked separately.
