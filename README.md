# Salesforce Dev Assistant — QLoRA Fine-Tuning


This project is my first attempt at training a model. Since I have a Salesforce background, I thought it would be nice to try to create a model that could assist a developer in writing code to be deployed to a Salesforce org: APEX code, triggers, Lightning Web Components.

In this project we are going to: 
- generate a series of prompts
- generate some gold examples matching these prompts
- iterate to generate some training data
- run the data in colab (https://colab.research.google.com/) with OLlama to build our model
- test our model

This is a proof of concept with a small Low Rank Adaptation (LoRA)



Fine-tunes `meta-llama/Llama-3.2-3B-Instruct` with QLoRA on a synthetic dataset of Salesforce
development tasks (Apex, SOQL, Lightning Web Components), producing a measurable before/after
comparison against the base model.

## Status

✅ The dataset, training, and evaluation are complete — the fine-tuned model shows a clear
quality improvement over the base (see Results below). 

## Repo layout

```
data/         raw + processed instruction datasets (JSONL)
src/          training and data-generation scripts
notebooks/    Colab/Kaggle training notebooks
eval/         before/after evaluation harness and results
models/       local model artifacts (gitignored)
```

## Setup

Local (data prep, demo, eval orchestration):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Training (Colab/Kaggle GPU runtime only):

```bash
pip install -r requirements-colab.txt
```

## Generating synthetic training data (Story B2)

Requires Ollama running locally with `llama3.2` pulled:

```bash
ollama pull llama3.2   # if not already present
pip install -r requirements.txt
python src/generate_data.py --per-category 25
```

This generates up to 25 examples per category (200 total) into `data/raw/generated.jsonl`,
using the 16 hand-written examples in `data/gold/gold_examples.jsonl` as few-shot style
guides. Re-run with a higher `--per-category` to grow the dataset, or `--category <name>`
to target one category at a time. Duplicate instructions (vs. gold + already-generated)
are detected and retried automatically.

## Results

Evaluated base vs. fine-tuned `meta-llama/Llama-3.2-3B-Instruct` on 23 Apex/SOQL/LWC
prompts (15 held-out + 8 baseline), scored 1–5 on correctness, Salesforce convention
adherence, and completeness. Full breakdown and methodology: [`eval/results/summary.md`](eval/results/summary.md).

| | Correctness | Convention | Completeness | Overall |
|---|---|---|---|---|
| Base model | 1.35 | 1.61 | 2.09 | 1.68 |
| Fine-tuned model | 2.57 | 3.22 | 3.00 | **2.93** |

**Win / tie / loss:** 16 / 6 / 1 in favor of the fine-tuned model.

Highlights:
- Fine-tuned model eliminated a repetition-loop failure mode that produced unusable output on 4/23 base-model responses, and ran ~1.7x faster as a result (9.8 min vs. 17.0 min for all 25 prompts).
- Largest gains on flagged weak categories from earlier dataset iterations: `apex_trigger` (1.83 → 3.83) and `apex_async` (1.56 → 2.89).
- No catastrophic forgetting — both models answered general-knowledge sanity prompts correctly.
