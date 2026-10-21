# Data sources

| Source | Version pinned | License | Local path | Used for |
|---|---|---|---|---|
| [AgentDojo](https://github.com/ethz-spylab/agentdojo) | commit `089ed468cf3ed0322acc66b0211f26d9d90dbf60` (2026-06-02), package v0.1.35 | MIT (LICENSE file, read 2026-10-21) | `data/raw/agentdojo` (gitignored) | Saved runs → dataset; workspace tool signatures; evaluation environment |
| [InjecAgent](https://github.com/uiuc-kang-lab/InjecAgent) | not cloned yet | MIT per GitHub repo page (file is named `LICENCE`; not read in full yet) | — | Supplementary attack examples (planned) |
| [BIPIA](https://github.com/microsoft/BIPIA) | not used yet | not checked | — | Optional |

## Reproduce

```bash
git clone https://github.com/ethz-spylab/agentdojo data/raw/agentdojo
```

```bash
git -C data/raw/agentdojo checkout 089ed468cf3ed0322acc66b0211f26d9d90dbf60
```

```bash
.venv\Scripts\python scripts\build_agentdojo_dataset.py --runs data\raw\agentdojo\runs --out data\processed\agentdojo_workspace.jsonl
```

Rebuilt 2026-10-21 at the pinned commit: 16,856 run files → 24,021 records (train 15,474 / val 4,189 /
test 4,358), 5,540 `needs_review`. Same as the counts in `dataset_and_training.md`.

## Notes
- AgentDojo's MIT license permits redistributing derived records with the copyright notice. The generated
  JSONL is still gitignored because it is reproducible from the pinned commit; human annotations
  (`data/processed/annotations/`) are tracked.
- Saved runs mix benchmark versions across agent models; record the version per record before mixing.
