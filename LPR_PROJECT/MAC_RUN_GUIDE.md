# Run the integrated simulation on this Mac

1. Keep the complete `LPR_PROJECT` folder together.
2. Double-click `RUN_COMPLETE_PROJECT.command`.
3. Wait for the terminal to finish. The full sequence performs fresh Elmer planar solves and refinement, all 16 native LTspice fault cases (including authorized repeats), Python decisions, three additional 3D cuboid benchmarks, and a literature-dimensioned parallel-plate benchmark through the electrical chain.
4. The completion report opens automatically. Results are saved in a new `evidence/mac_completion/<timestamp>/` folder. A failed run retains a FAILED record and never reuses old solver results.

Terminal equivalent, from this directory:

```sh
.venv/bin/python code/complete_mac.py
```

For one case with fresh planar FEM:

```sh
./RUN_SIMULATION.command --case nominal_healthy
```

For the 16-case campaign only:

```sh
./RUN_SIMULATION.command --matrix
```

`CHECK_PROJECT.command` verifies package integrity and runs 206 Python checks. The installed `.venv` contains the required scientific packages. To recreate it, use Python 3.13, `python3 -m venv .venv`, then `.venv/bin/python -m pip install -r requirements.txt`.

Required native applications: LTspice at `/Applications/LTspice.app/Contents/MacOS/LTspice` and ElmerSolver at `/opt/homebrew/bin/ElmerSolver`. The LTspice executable can be overridden using `LPR_LTSPICE_EXECUTABLE`. Gmsh, Wine and a CrossOver bottle are no longer needed for these runs. The planar refinement subdivides the supplied connected mesh directly and preserves body and boundary identities.

The LOG reader supports native Mac UTF-16LE without BOM. End-time checking permits a maximum 10 microsecond solver endpoint discrepancy (versus the 50 millisecond acquisition period); observations are selected from native clock edges without resampling. Repeat prefixes retain the existing strict equality checks.

# Published geometry and scope

The added reference model takes the 10 cm square electrode faces and 12 mm gap from COMSOL's published **Soluble Lead–Acid Redox Flow Battery**, Cell Geometry and Mesh:
https://doc.comsol.com/6.3/doc/com.comsol.help.models.battery.pb_flow_battery/pb_flow_battery.html

That model cites Willis et al., *J. Appl. Electrochem.* 40, 955–965 (2010), and Shah et al., *J. Electrochem. Soc.* 157, A589–A599 (2010). Dimensions here are attributed to the COMSOL model; reproduction of those research papers is not claimed.

Our explicitly reduced model solves uniform ohmic conduction with ideal full-face electrodes. Conductivity is assumed to be 1 S/m, and a nonloading midpoint reference is added for the LPR interface. The flow, reactions, changing electrolyte concentration, and inlet/outlet edge extensions of the published model are omitted. Two native 3D meshes are checked against the affine field and analytical resistance, with independent energy and boundary-flux calculations. This verifies the numerical implementation under these assumptions.

**Physical specimen qualification remains open.** A published geometry is a reference specimen design, not evidence of the dimensions or behavior of a real specimen. Physical qualification needs actual geometry and exposed area, electrolyte conductivity and temperature, electrode/reference placement, and independent measured resistance or polarization data. Neither the inherited 3D specimen nor material corrosion rate is certified by these simulation results.

# Delivered validation

`evidence/mac_completion/verified_delivery/index.html` links to the completed fresh campaign, independent release checks, and 3D benchmark records. Historical project evidence remains separate. The supplied scientific `project/` sources were not changed. Modified integration code and added validation scripts are listed in `MAC_COMPLETION_CHANGES.json`.
