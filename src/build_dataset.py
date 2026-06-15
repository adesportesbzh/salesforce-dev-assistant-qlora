"""
B3 — Clean, dedupe, and split the dataset.

Combines every `data/raw/generated*.jsonl` file with the hand-written gold examples
(`data/gold/gold_examples.jsonl`), and produces the final training files:

  data/processed/train.jsonl
  data/processed/val.jsonl
  data/processed/test.jsonl

Steps:
1. Load all raw generated examples + gold examples.
2. Drop any example that fails the heuristic checks in validate_examples.py
   (SOQL/DML in a loop, known-invalid Apex syntax, unbalanced delimiters, etc.).
3. Dedupe by normalized instruction text — both across the raw files (which may
   overlap, since several B2 runs were kept) and against the gold examples and the
   held-out baseline_prompts.json (eval contamination guard).
4. Re-assign clean, sequential ids per category.
5. Shuffle (fixed seed for reproducibility) and split per category into
   train (~85%) / val (~10%) / test (~5%), so every category is represented in
   each split even though the dataset is small.

Usage:
    python src/build_dataset.py
    python src/build_dataset.py --seed 42 --val-frac 0.10 --test-frac 0.05
"""

import argparse
import json
import random
import re
from pathlib import Path

from validate_examples import validate_example

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data" / "gold" / "gold_examples.jsonl"
RAW_DIR = ROOT / "data" / "raw"
BASELINE_PATH = ROOT / "data" / "baseline_prompts.json"
PROCESSED_DIR = ROOT / "data" / "processed"


def normalize(text):
    return re.sub(r"\s+", " ", text.strip().lower())


def load_jsonl(path):
    examples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-frac", type=float, default=0.10)
    parser.add_argument("--test-frac", type=float, default=0.05)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    gold = load_jsonl(GOLD_PATH)
    raw_files = sorted(RAW_DIR.glob("generated*.jsonl"))

    with open(BASELINE_PATH) as f:
        baseline = json.load(f)
    excluded_instructions = {normalize(p["instruction"]) for p in baseline}

    # Seed `seen` with gold + baseline so generated examples that duplicate either
    # are dropped.
    seen = {normalize(ex["instruction"]) for ex in gold} | excluded_instructions

    by_category = {}  # category -> list of clean, deduped examples (re-id'd later)
    stats = {}  # category -> {raw, dup, invalid, kept}

    for path in raw_files:
        for ex in load_jsonl(path):
            cat = ex["category"]
            s = stats.setdefault(cat, {"raw": 0, "dup": 0, "invalid": 0, "kept": 0})
            s["raw"] += 1

            norm = normalize(ex["instruction"])
            if norm in seen:
                s["dup"] += 1
                continue

            issues = validate_example(ex)
            if issues:
                s["invalid"] += 1
                continue

            seen.add(norm)
            s["kept"] += 1
            by_category.setdefault(cat, []).append(ex)

    print(f"Loaded {len(raw_files)} raw file(s): {', '.join(p.name for p in raw_files)}\n")
    print(f"{'category':<18} {'raw':>5} {'dup':>5} {'invalid':>8} {'kept':>5}")
    for cat in sorted(stats):
        s = stats[cat]
        print(f"{cat:<18} {s['raw']:>5} {s['dup']:>5} {s['invalid']:>8} {s['kept']:>5}")

    total_kept = sum(len(v) for v in by_category.values())
    total_gold = len(gold)
    print(f"\n{total_kept} generated examples kept (after dedup + validation) + {total_gold} gold examples")

    # Re-assign sequential ids per category, build combined per-category lists
    # (gold first, then clean generated).
    combined_by_category = {}
    for ex in gold:
        combined_by_category.setdefault(ex["category"], []).append(ex)
    for cat, exs in by_category.items():
        for i, ex in enumerate(exs, 1):
            ex = dict(ex)
            ex["id"] = f"{cat}-clean-{i:03d}"
            combined_by_category.setdefault(cat, []).append(ex)

    train, val, test = [], [], []
    print(f"\n{'category':<18} {'total':>5} {'train':>6} {'val':>5} {'test':>5}")
    for cat in sorted(combined_by_category):
        examples = combined_by_category[cat][:]
        rng.shuffle(examples)
        n = len(examples)

        n_test = max(1, round(n * args.test_frac)) if n >= 4 else 0
        n_val = max(1, round(n * args.val_frac)) if n >= 4 else 0
        n_train = n - n_val - n_test

        train.extend(examples[:n_train])
        val.extend(examples[n_train:n_train + n_val])
        test.extend(examples[n_train + n_val:])
        print(f"{cat:<18} {n:>5} {n_train:>6} {n_val:>5} {n - n_train - n_val:>5}")

    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for name, examples in (("train", train), ("val", val), ("test", test)):
        out_path = PROCESSED_DIR / f"{name}.jsonl"
        with open(out_path, "w") as f:
            for ex in examples:
                f.write(json.dumps(ex) + "\n")
        print(f"\nWrote {len(examples)} examples to {out_path}")


if __name__ == "__main__":
    main()
