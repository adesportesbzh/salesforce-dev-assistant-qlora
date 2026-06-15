# Salesforce Dev Assistant — QLoRA Fine-Tuning

Fine-tunes `meta-llama/Llama-3.2-3B-Instruct` with QLoRA on a synthetic dataset of
Salesforce development tasks (Apex, SOQL, Lightning Web Components), producing a
fine-tuned model that measurably outperforms the base model on Salesforce-specific
coding tasks.

This is my first hands-on model fine-tuning project. Coming from a Salesforce
development background, I wanted to build (and be able to explain end-to-end) the
workflow that's behind most "fine-tuned assistant" job postings I've been seeing:
generating a training set, validating it, fine-tuning a quantized model with QLoRA on
free Colab GPU, and proving the result with a structured before/after evaluation —
rather than just reading about how it's done.

For the detailed build log — prompt-tuning iterations, dead ends, training run
details, and evaluation results — see [JOURNEY.md](JOURNEY.md).

## Status

✅ Dataset generation, fine-tuning, and evaluation are complete. The fine-tuned model
shows a clear quality improvement over the base model (see [Results](#results)).

## What I practiced

This was my first time working through this kind of workflow, so the goal was less
about producing a polished model and more about understanding — and being able to
walk through — each step:

- **Synthetic data generation** — using a local LLM (Ollama / Llama 3.2) to expand a
  small hand-written seed set into a larger instruction dataset, few-shot style.
- **Data validation & curation** — heuristic checks for invalid/anti-pattern Apex
  (e.g. SOQL or DML inside loops, invented syntax), deduplication, and a
  train/val/test split that avoids eval contamination.
- **QLoRA fine-tuning** — 4-bit quantized base model + LoRA adapters, trained on a
  single Colab T4 GPU with `transformers`, `peft`, `trl`, and `bitsandbytes`.
- **Evaluation methodology** — a reproducible before/after harness comparing base vs.
  fine-tuned on held-out tasks, scored against a rubric (correctness, convention
  adherence, completeness), plus a catastrophic-forgetting check.

## How it works (pipeline)

1. **Hand-write gold examples** — 16 examples across 8 task categories (`apex_class`,
   `apex_trigger`, `apex_test`, `apex_async`, `soql`, `lwc`, `validation_rule`,
   `best_practices`) in [`data/gold/gold_examples.jsonl`](data/gold/gold_examples.jsonl).
2. **Generate synthetic training data** — `src/generate_data.py` prompts a local
   Ollama model (`llama3.2`) to produce more examples per category, using the gold
   examples as few-shot guides.
3. **Validate & clean** — `src/validate_examples.py` filters out generated examples
   with structurally invalid or anti-pattern Apex/SOQL.
4. **Build the final dataset** — `src/build_dataset.py` combines gold + validated
   generated examples, dedupes, and splits into `train` / `val` / `test`
   (`data/processed/`).
5. **Fine-tune with QLoRA** — [`notebooks/02_train_qlora.ipynb`](notebooks/02_train_qlora.ipynb),
   run on a Colab GPU runtime, produces a LoRA adapter on top of the 4-bit base model.
6. **Evaluate before vs. after** — [`notebooks/03_eval.ipynb`](notebooks/03_eval.ipynb)
   runs the base model and the fine-tuned model on the same 25 prompts (15 held-out +
   8 baseline + 2 general-knowledge sanity checks) and saves the results to
   [`eval/results/comparison.json`](eval/results/comparison.json).
7. **Score the results** — each response is scored 1–5 on correctness, convention
   adherence, and completeness. Full breakdown:
   [`eval/results/summary.md`](eval/results/summary.md).

## Results

Evaluated base vs. fine-tuned `meta-llama/Llama-3.2-3B-Instruct` on 23 Apex/SOQL/LWC
prompts (15 held-out + 8 baseline), scored 1–5 on correctness, Salesforce convention
adherence, and completeness. Full breakdown and methodology:
[`eval/results/summary.md`](eval/results/summary.md).

| | Correctness | Convention | Completeness | Overall |
|---|---|---|---|---|
| Base model | 1.35 | 1.61 | 2.09 | 1.68 |
| Fine-tuned model | 2.57 | 3.22 | 3.00 | **2.93** |

**Win / tie / loss:** 16 / 6 / 1 in favor of the fine-tuned model.

Highlights:
- Fine-tuned model eliminated a repetition-loop failure mode that produced unusable
  output on 4/23 base-model responses, and ran ~1.7x faster as a result (9.8 min vs.
  17.0 min for all 25 prompts).
- Largest gains on flagged weak categories: `apex_trigger` (1.83 → 3.83) and
  `apex_async` (1.56 → 2.89).
- No catastrophic forgetting — both models answered general-knowledge sanity prompts
  correctly.

## Repo layout

```
data/         raw + processed instruction datasets (JSONL)
src/          data-generation, validation, and dataset-building scripts
notebooks/    Colab notebooks for fine-tuning and evaluation
eval/         before/after evaluation harness and results
models/       local model artifacts (gitignored)
```

## Setup

Local (data prep, eval orchestration):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Training/eval notebooks (Colab GPU runtime only):

```bash
pip install -r requirements-colab.txt
```

### Generating synthetic training data

Requires Ollama running locally with `llama3.2` pulled:

```bash
ollama pull llama3.2   # if not already present
pip install -r requirements.txt
python src/generate_data.py --per-category 25
```

This generates up to 25 examples per category (200 total) into
`data/raw/generated.jsonl`, using the 16 hand-written examples in
`data/gold/gold_examples.jsonl` as few-shot style guides. Re-run with a higher
`--per-category` to grow the dataset, or `--category <name>` to target one category at
a time. Duplicate instructions (vs. gold + already-generated) are detected and retried
automatically.
