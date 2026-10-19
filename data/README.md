# Local assignment inputs

From the repository root, create `data/private/` and place the supplied files as:

```text
data/private/
├── PJP_Assignment_Sample_Data.xlsx
└── PJP_Simulator/
    └── simulate.py
```

Extract the simulator ZIP first. Its README and example callback may remain in
the same folder. Do not introduce an extra nested `PJP_Simulator` directory.
The workbook and simulator are required; the brief and video are not runtime
inputs. These confidential files are Git-ignored and are not included in the
reviewer bundle.

After installing the project as explained in the repository README, run from
the repository root:

```bash
python -m pjp run
```

See `docs/data_handling.md` for confidentiality and local-data handling.
