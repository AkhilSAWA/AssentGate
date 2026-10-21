"""Train the AssentGate authorization classifier on the JSONL from build_agentdojo_dataset.py.

Two models, same input text and same metrics:
  --model lr       sentence embeddings + logistic regression (baseline, CPU, minutes)
  --model deberta  microsoft/deberta-v3-small fine-tune (main model; GPU recommended)

Ablation (evaluation baseline 4): add --no-evidence to drop provenance + history from the input.

Install:  pip install -e ".[ml]"   (torch, transformers, sentence-transformers, scikit-learn)
Run:      python scripts/train_classifier.py --data data/processed/agentdojo_workspace.jsonl --model lr
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

LABELS = ["authorized", "safely_derived", "confirmation_required", "blocked"]
L2I = {l: i for i, l in enumerate(LABELS)}
ALLOW = {L2I["authorized"], L2I["safely_derived"]}


def _short(v, n=160):
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[:n] + "…"


def to_text(r: dict, use_evidence: bool = True) -> str:
    args = "; ".join(f"{k}={_short(v)}" for k, v in (r.get("args") or {}).items())
    parts = [f"[REQUEST] {r['user_request']}", f"[TOOL] {r['tool']}", f"[ARGS] {args}"]
    if use_evidence:
        ev = "; ".join(f"{k}<-{v}" for k, v in (r.get("evidence") or {}).items()) or "none"
        hist = " > ".join(h["tool"] for h in r.get("history") or []) or "none"
        parts += [f"[EVIDENCE] {ev}", f"[HISTORY] {hist}"]
    return "\n".join(parts)


READ_ONLY_PREFIXES = ("get_", "search_", "list_")


def load(path: Path, include_unreviewed: bool, side_effects_only: bool = False):
    """Rows with a human `reviewed_label` always count; rule labels flagged needs_review only
    with --include-unreviewed. NOTE: every confirmation_required rule label is needs_review,
    so that class only exists after human review (or with --include-unreviewed)."""
    rows = [json.loads(l) for l in path.open(encoding="utf-8")]
    out = {"train": [], "val": [], "test": []}
    for r in rows:
        if side_effects_only and r["tool"].startswith(READ_ONLY_PREFIXES):
            continue
        if r["needs_review"] and not include_unreviewed and not r.get("reviewed_label"):
            continue
        r["y"] = L2I[r.get("reviewed_label") or r["label"]]
        out[r["split"]].append(r)
    return out


def report(name: str, y_true, y_pred) -> dict:
    from sklearn.metrics import classification_report, f1_score

    n = len(y_true)
    unsafe = sum(t not in ALLOW and p in ALLOW for t, p in zip(y_true, y_pred))
    false_block = sum(t != L2I["blocked"] and p == L2I["blocked"] for t, p in zip(y_true, y_pred))
    unnecessary = sum(t in ALLOW and p == L2I["confirmation_required"] for t, p in zip(y_true, y_pred))
    m = {
        "macro_f1": f1_score(y_true, y_pred, average="macro", labels=range(4), zero_division=0),
        "unsafe_allow_rate": unsafe / max(1, sum(t not in ALLOW for t in y_true)),
        "false_block_rate": false_block / max(1, sum(t != L2I["blocked"] for t in y_true)),
        "unnecessary_confirmation_rate": unnecessary / max(1, sum(t in ALLOW for t in y_true)),
        "n": n,
    }
    print(f"\n== {name} ==")
    print(classification_report(y_true, y_pred, labels=range(4), target_names=LABELS, zero_division=0))
    print({k: round(v, 4) if isinstance(v, float) else v for k, v in m.items()})
    return m


def train_lr(data, use_evidence, out_dir: Path):
    import joblib
    from sentence_transformers import SentenceTransformer
    from sklearn.linear_model import LogisticRegression

    enc = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    X = {s: enc.encode([to_text(r, use_evidence) for r in data[s]], batch_size=64, show_progress_bar=True)
         for s in data}
    y = {s: [r["y"] for r in data[s]] for s in data}
    clf = LogisticRegression(max_iter=2000, class_weight="balanced").fit(X["train"], y["train"])
    res = {s: report(f"lr/{s}", y[s], clf.predict(X[s]).tolist()) for s in ("val", "test")}
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(clf, out_dir / "lr.joblib")
    return res


def train_deberta(data, use_evidence, out_dir: Path, epochs: int, bs: int, lr: float):
    import numpy as np
    import torch
    from torch.utils.data import Dataset
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer, Trainer,
                              TrainingArguments)

    name = "microsoft/deberta-v3-small"
    tok = AutoTokenizer.from_pretrained(name)

    class DS(Dataset):
        def __init__(self, rows):
            self.enc = tok([to_text(r, use_evidence) for r in rows], truncation=True, max_length=384)
            self.y = [r["y"] for r in rows]

        def __len__(self):
            return len(self.y)

        def __getitem__(self, i):
            item = {k: torch.tensor(v[i]) for k, v in self.enc.items()}
            item["labels"] = torch.tensor(self.y[i])
            return item

    ds = {s: DS(data[s]) for s in data}
    counts = Counter(ds["train"].y)
    weights = torch.tensor([len(ds["train"]) / (4 * max(1, counts[i])) for i in range(4)], dtype=torch.float)

    class WeightedTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, **kw):
            labels = inputs.pop("labels")
            out = model(**inputs)
            loss = torch.nn.functional.cross_entropy(out.logits, labels, weight=weights.to(out.logits.device))
            return (loss, out) if return_outputs else loss

    def metrics(p):
        from sklearn.metrics import f1_score
        return {"macro_f1": f1_score(p.label_ids, np.argmax(p.predictions, -1), average="macro")}

    model = AutoModelForSequenceClassification.from_pretrained(
        name, num_labels=4, id2label=dict(enumerate(LABELS)), label2id=L2I)
    args = TrainingArguments(
        output_dir=str(out_dir), num_train_epochs=epochs, per_device_train_batch_size=bs,
        per_device_eval_batch_size=bs * 2, learning_rate=lr, weight_decay=0.01, warmup_ratio=0.1,
        eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
        metric_for_best_model="macro_f1", save_total_limit=1, logging_steps=50, report_to=[],
        fp16=torch.cuda.is_available(), seed=13)
    trainer = WeightedTrainer(model=model, args=args, train_dataset=ds["train"],
                              eval_dataset=ds["val"], compute_metrics=metrics)
    trainer.train()
    res = {}
    for s in ("val", "test"):
        pred = trainer.predict(ds[s])
        res[s] = report(f"deberta/{s}", pred.label_ids.tolist(), np.argmax(pred.predictions, -1).tolist())
    trainer.save_model(str(out_dir / "best"))
    tok.save_pretrained(str(out_dir / "best"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--model", choices=["lr", "deberta"], default="lr")
    ap.add_argument("--no-evidence", action="store_true", help="ablation: request+tool+args only")
    ap.add_argument("--include-unreviewed", action="store_true",
                    help="also train on rule labels flagged needs_review (noisier)")
    ap.add_argument("--side-effects-only", action="store_true",
                    help="drop read-only calls (they are ~80%% of records and trivially easy)")
    ap.add_argument("--out", type=Path, default=Path("models"))
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=3e-5)
    a = ap.parse_args()

    data = load(a.data, a.include_unreviewed, a.side_effects_only)
    for s, rows in data.items():
        print(s, len(rows), dict(Counter(LABELS[r["y"]] for r in rows)))
    missing = [l for l in LABELS if not any(r["y"] == L2I[l] for r in data["train"])]
    if missing:
        print(f"WARNING: no training examples for {missing}. Review records (set reviewed_label) "
              "or pass --include-unreviewed.")
    tag = (a.model + ("-noevidence" if a.no_evidence else "") + ("-sideeffects" if a.side_effects_only else "")
           + ("-weaklabels" if a.include_unreviewed else ""))
    out = a.out / tag
    res = (train_lr(data, not a.no_evidence, out) if a.model == "lr"
           else train_deberta(data, not a.no_evidence, out, a.epochs, a.batch_size, a.lr))
    out.mkdir(parents=True, exist_ok=True)
    res["config"] = {k: str(v) for k, v in vars(a).items()}
    res["label_counts"] = {s: dict(Counter(LABELS[r["y"]] for r in rows)) for s, rows in data.items()}
    (out / "metrics.json").write_text(json.dumps(res, indent=2))
    print(f"\nsaved to {out}")


if __name__ == "__main__":
    main()
