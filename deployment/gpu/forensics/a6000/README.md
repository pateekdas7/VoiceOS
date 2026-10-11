# A6000 GPU forensic package

Staging tree assembled pre-rental for the A6000 forensic run
(task #49 in the running task list). Ended up executed on the rented L4
instead when the A6000 slot fell through, so the archive filename is
`L4_FORENSIC_ARCHIVE_2026-08-27.tar.gz` (not stored here — too large;
regenerable from the parts below by rerunning them).

## Parts

| Directory | Purpose |
|---|---|
| `part_a_t4/` | T4×2 baseline fingerprint (Kaggle-side reference) |
| `part_b_l4/` | L4 environment prep + current-code BF16 SDPA forensic |
| `part_c_a6000/` | Historical L4 setup reconstructed from git — the "was it always like this?" experiment |
| `part_d_tests/` | Cross-run tests (T4 vs L4 vs historical-L4) |
| `part_e_compare/` | Three-way comparison report + regression matrix |
| `part_f_fourapproach/` | Expanded four-approach A/B corpus + harness + report |
| `part_g_v2validation/` | V2 validation corpus (~130 texts) + harness + final report |
| `report/` | Consolidated forensic writeups |

Model checkpoints under `**/hf/**`, `.tar.gz` archives, and any results
JSON > 500 kB were excluded — regeneratable by rerunning the scripts in
each part. The small summary JSONs are kept.
