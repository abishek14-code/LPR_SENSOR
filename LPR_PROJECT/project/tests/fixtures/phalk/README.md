# Exact supplied PHALK test artifact

`alkalinity_SurfaceWaters.csv` is an unchanged copy of the user-supplied artifact:
50,916 records, 25 columns, 8,090,850 bytes.

SHA-256: `2dce7c0a98bd6d661857dd0fa6e45933b5674e7dde906428ffb5e2a92547c3a7`.

Attribution: Meritxell Batalla, Jordi Martínez-Artero and Jordi Catalan (2026),
*Global basin-scale mapping of pH and alkalinity in inland waters*, Scientific Data
13:686, https://doi.org/10.1038/s41597-026-07028-2.
Dataset: PHALK, https://doi.org/10.5281/zenodo.15591825. The paper states CC BY 4.0
for the dataset. No changes, filtering or corrections were made to this copy.

The artifact has five `value=200` records labelled `a) < 200`. Tests explicitly
verify their detection. These are dataset findings, not failing software tests.
The accompanying paper is the source of units and validation rules; see
`docs/phalk_dataset_contract.md` and the P3 validation report for limitations.

The local `.gitattributes` disables line-ending conversion for this CSV only,
preserving the pinned artifact bytes across Git checkout environments.
