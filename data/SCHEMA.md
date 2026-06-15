# Dataset Schema

All training data is stored as JSONL (one JSON object per line) — the format expected by
Hugging Face `datasets` and TRL's `SFTTrainer`. There is no database; everything lives in
flat files under `data/`.

## Record format

```json
{
  "id": "apex-trigger-discount-0007",
  "category": "apex_trigger",
  "instruction": "Write an Apex trigger on Opportunity that applies a 10% discount to Amount when StageName changes to 'Negotiation'.",
  "input": "",
  "output": "trigger OpportunityDiscount on Opportunity (before update) {\n    ...\n}"
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | Unique, human-readable (`{category}-{slug}-{seq}`) |
| `category` | string | One of the 8 task categories below |
| `instruction` | string | The natural-language task, as a developer would phrase it |
| `input` | string | Optional extra context (e.g., existing code to refactor). Empty string `""` when not needed |
| `output` | string | Target response — code plus brief explanation where useful |

## Categories

Matches the 8 categories used in `data/baseline_prompts.json`:

- `apex_trigger` — triggers on standard/custom objects
- `soql` — queries, including relationships and aggregates
- `apex_class` — utility classes, patterns, services
- `validation_rule` — formula-based validation rules
- `apex_async` — Queueable, Batchable, Schedulable, @future
- `lwc` — Lightning Web Components (+ Apex controller pairs)
- `apex_test` — `@isTest` test classes
- `best_practices` — explanations + bulkification/refactoring tasks

## Files

```
data/
  gold/
    gold_examples.jsonl   16 hand-written examples (2 per category) — Story B1
  raw/
    generated*.jsonl      synthetic examples from B2 runs, unfiltered (one file per
                          generation run; build_dataset.py combines + dedupes all of them)
  processed/
    train.jsonl           ~85% of cleaned data (per-category split)
    val.jsonl             ~10%
    test.jsonl            ~5%, held out — never used in training
  baseline_prompts.json   8 prompts used for the A2 smoke test and reused as
                          part of the held-out eval set (Epic D)
```

`gold_examples.jsonl` is distinct from `baseline_prompts.json` so the eval set isn't
contaminated by examples that may also be folded into training.

## Acquisition

No scraping of Salesforce docs/Trailhead or proprietary employer code — both for
copyright and quality reasons. Everything is synthetic:

1. **Gold examples** (this story): hand-written by us, used as a style/quality anchor
   and as few-shot examples for synthetic generation.
2. **Synthetic generation** (B2): prompt a local model (Ollama `llama3.2`) with the
   gold examples as few-shot guides, looped over varied task templates per category,
   to produce instruction/output pairs.
3. **Validation + dedup** (B3): `src/validate_examples.py` heuristically filters out
   examples with invalid Apex (SOQL/DML inside loops, unbalanced delimiters, known
   bad patterns); `src/build_dataset.py` dedupes against gold + the held-out
   `baseline_prompts.json` and splits into train/val/test.
