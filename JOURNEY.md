# Project Journey

This is the detailed build log behind [README.md](README.md) — the iterations,
failed attempts, and design decisions that led to the final pipeline and results.
This is a small proof-of-concept project, and a lot of the value for me was in
working through *why* early attempts didn't work and what that implied for the next
step.

## Generating synthetic training data

`src/generate_data.py` prompts a local Ollama model (`llama3.2`) to generate
instruction/input/output examples per category, using the 16 hand-written gold
examples in `data/gold/gold_examples.jsonl` as few-shot guides (see
[README setup](README.md#generating-synthetic-training-data) for how to run it).
Getting this script to reliably produce *valid* Apex took three rounds of prompt
iteration.

### v1 — first test run

**Context:** First test run of `--per-category 5` (40 examples, 0 hard failures —
all examples parsed successfully into the INSTRUCTION/INPUT/OUTPUT format).

**Observation:** Parsing succeeded, but a manual review of the generated *content*
found real Apex correctness issues in roughly 2/3 of a small sample:

- `apex_trigger-gen-0001`: used `throw new ApexException(...)` inside a trigger.
  `ApexException` isn't a built-in Apex class, and triggers should block saves via
  `record.addError(...)`, not by throwing. The same example also had
  `for (Account acc : Trigger.oldMap.get(acc.Id))` — `Trigger.oldMap.get(id)` returns
  a single record, not a collection, so this can't be a `for`-loop target. It also
  put a SOQL query inside a `for` loop — the exact anti-pattern the `best_practices`
  category is supposed to teach against.
- `apex_trigger-gen-0003`: generated `so.Total Items Shipped = 0;` — a field
  reference with spaces in the name, which is not valid Apex/SOQL syntax (custom
  fields should be `Total_Items_Shipped__c`).
- `apex_trigger-gen-0002`: mostly correct, minor naming convention issue
  (`ResolutionStatus` vs. the `__c` suffix convention for custom fields).

**Root cause:** `llama3.2:3B` is a small model with no compiler feedback loop, so it
readily produces code that *looks* plausible but doesn't compile — wrong field-naming
conventions, invalid loop targets, and incorrect error-handling patterns in triggers.

**Decision:**
1. Tighten the system prompt in `src/generate_data.py` with an explicit "common
   pitfalls" checklist covering the issues above (custom field naming, `addError()`
   vs. `throw`, no SOQL/DML in loops, `Trigger.oldMap`/`newMap` semantics, balanced
   braces/parens/brackets, scope correctness).
2. Re-run a small `--per-category 5` batch and compare quality before committing to a
   full-size generation run.
3. Plan to add heuristic structural validation later (balanced delimiters, no spaces
   in dotted identifiers, no SOQL/DML textually inside a `for { ... }` block) to catch
   whatever still slips through — a 3B model will never be 100% reliable.

### v2 — pitfalls checklist

**Context:** Re-ran `--per-category 5` with the v2 prompt (pitfalls checklist added).
Reviewed the 5 fresh `apex_trigger` examples (`apex_trigger-gen-0001..0005`).

**Observation:** Only 1/5 (`...0004`, the `Trigger.oldMap.get()` example) was clean.
The other 4 still had serious issues, several of them the *exact* pitfalls the
checklist called out:

- `...0001`: `for (Contact con : acc CONTACTS__c)` (missing dot, made-up relationship
  syntax), and `insert AccountBlock record (Id Id, Boolean Blocked) values ([: ...])`
  — SQL `INSERT ... VALUES` syntax, not valid Apex DML. Also `Trigger.new = []`
  (Trigger context variables aren't assignable) and `update blockedAccountIds` (a
  `Set<Id>`, not an sObject list).
- `...0002` and `...0005`: SOQL queries still placed inside `for` loops — the #1 item
  on the checklist, ignored.
- `...0003`: invented a non-existent `setOf(...)` function, and referenced the loop
  variable `ord` outside its loop's scope.

**Root cause:** A plain-language "don't do X" checklist isn't enough signal for a 3B
model on a generative task this open-ended — it can recite the rule and violate it in
the same response. Telling it what's wrong in the abstract doesn't transfer well;
concrete before/after examples of *exactly these* mistakes are likely to work better
(closer to the few-shot examples it's already copying style from).

**Decision (v3):**
1. Add a short "known mistakes from earlier runs, with corrections" block to the
   system prompt, using the actual bugs found above as worked before/after pairs
   (SQL-style `INSERT...VALUES`, SOQL-in-loop, spaces in custom field names,
   `Trigger.oldMap.get()` misuse).
2. Lower the default `--temperature` from 0.9 to 0.6 for more deterministic output.
3. If quality is still too low after v3, the likely next step is **not** more prompt
   tweaking but either (a) an automated validator (heuristic structural checks, or a
   real Apex parser like PMD's `apex` ruleset) that filters/discards bad examples
   before they reach the training set, and/or (b) narrowing the fine-tuning task to
   something more tractable for a 3B model (e.g., SOQL generation, code review /
   anti-pattern explanation) rather than full Apex class/trigger generation from
   scratch.

### v3 — anti-pattern examples + lower temperature

**Context:** Re-ran `--per-category 5` with the v3 prompt (anti-pattern before/after
examples + temperature 0.6). Reviewed the 5 fresh `apex_trigger` examples again.

**Observation:** 2/5 clean this time (`...0002`, `...0004`), up from 1/5 in v2.

- `...0002`: clean — simple `before update` field-setting trigger, no SOQL/DML.
- `...0004`: clean — correctly uses `Trigger.oldMap.get(acc.Id)` as a single record
  (this is almost a copy of the corrected example we put in the prompt, so the
  anti-pattern example worked for *this specific* pattern).
- `...0001`: the SOQL query is now correctly placed *before* the loop (progress!),
  but the example invents new syntax to "throw a custom error" —
  `trigger BlockOpenAccountDeletionError : '...' { throw new ... }` is not valid Apex
  (triggers can't be declared inside triggers), and
  `insert AccountBlock__c block = new AccountBlock__c(...)` mixes a declaration and a
  DML statement incorrectly.
- `...0003` and `...0005`: both still put SOQL *and* DML inside `for` loops — the
  single most emphasized pitfall, given its own worked example, still slips through
  in 2/5 examples.

**Root cause:** Prompt engineering has now had three iterations (checklist →
checklist + anti-examples → lower temperature) with diminishing returns: ~25% → ~20%
→ ~40% clean for this category, but SOQL/DML-in-loop remains the dominant failure
even when explicitly modeled. A 3B model generating ~30-60 line Apex classes from
scratch is just not reliable enough to skip a validation step.

**Decision:** Move to a cleaning/validation step now rather than continuing to tune
the prompt:
1. Build `src/validate_examples.py` — a heuristic structural validator for
   Apex-bearing categories (`apex_trigger`, `apex_class`, `apex_async`, `apex_test`):
   balanced braces/parens/brackets, and a brace-depth-aware scan that flags any
   `for (...) { ... }` block whose body contains a SOQL query (`[SELECT ...]`) or a
   top-level DML statement (`insert`/`update`/`delete`/`upsert`/`merge`).
2. Run the validator over `data/raw/generated.jsonl`, drop failures, and report the
   pass rate per category — this tells us the real yield and how much we need to
   over-generate by.
3. Since local generation is free (just time), generate in much larger batches (e.g.,
   `--per-category 60-100`) to compensate for an expected ~40-60% discard rate, then
   dedupe/merge with gold and split into train/val/test.
4. If the post-filter yield is still too low to reach a usable training set size,
   revisit scope (e.g., de-emphasize full class/trigger generation in favor of
   categories the model handles better, like `soql`/`validation_rule`/
   `best_practices`).

The goal throughout was a raw dataset that's cleaned, validated, and split into
train/validation/test sets, so that training is stable and evaluation is meaningful
and not contaminated by training data: filter out malformed/empty/near-duplicate
examples, check that code fields are syntactically plausible, split into train
(~85%), validation (~10%), and test (~5%, held out for final eval), and save the
processed splits to `data/processed/` as JSONL.

## Validator and first dataset build

Built `src/validate_examples.py` (heuristic checks: balanced braces/parens/brackets;
SOQL `[SELECT ...]` or DML insert/update/delete/upsert/merge inside a `for { ... }`
body; a handful of known-invalid patterns from earlier runs — `setOf(...)`,
reassigning `Trigger.new`/`Trigger.oldMap`, `ApexException`, SQL-style
`INSERT ... VALUES`, nested `trigger X : '...' { }`; and Title-Case "spaced field
name" detection for `apex_trigger`/`apex_class`/`apex_async`/`apex_test`).

First pass on `data/raw/generated.jsonl` (the v3 run): 36/40 (90%) clean overall, but
**`apex_trigger` was only 2/5 (40%) clean** — matches the manual review (`...0002`
and `...0004` clean; `...0001` invents a nested-trigger syntax, `...0003` and
`...0005` still have SOQL/DML inside loops).

Note: an early version of the "spaced field name" check had false positives on valid
code like `Messaging.SingleEmailMessage mail = ...` and `WHERE x.Id AND StageName = ...`
— fixed by requiring 2+ Title-Case words after the dot and excluding SOQL keywords
(AND/OR/WHERE/etc.).

Built `src/build_dataset.py` (combines all `data/raw/generated*.jsonl` — there are
now 3 files from the v1/v2/v3 runs, ~119 raw examples total — dedupes against gold +
each other + `baseline_prompts.json`, validates, re-ids, and splits into
train/val/test per category).

**Result of first build:**

| category | raw | invalid (dropped) | kept |
|---|---|---|---|
| apex_trigger | 15 | 10 (67%) | 5 |
| apex_async | 15 | 6 (40%) | 9 |
| apex_test | 15 | 1 | 14 |
| apex_class | 15 | 0 | 15 |
| best_practices | 15 | 0 (2 dup) | 13 |
| lwc | 15 | 0 | 15 |
| soql | 15 | 0 | 15 |
| validation_rule | 14 | 0 | 14 |

100 generated + 16 gold = **116 total**, split 94 / 14 / 8 (train/val/test).

**Observation:** `apex_trigger` and `apex_async` remain the weak spots — both involve
multi-object interactions (trigger touching a parent/related object, async job
querying/updating records), which seems to be exactly where the 3B model reaches for
SOQL-in-loop or invents syntax. The other 6 categories are close to 100% clean.

**Decision:** 116 examples is workable as a first end-to-end pass (LoRA fine-tunes
can work with small datasets), but thin — especially for the two weak categories.
Plan: run a larger batch (e.g. `--per-category 30`) with the v3 prompt to grow
`apex_trigger`/`apex_async` specifically, re-run `build_dataset.py`, and re-evaluate
before moving on to fine-tuning. If `apex_trigger`'s clean rate doesn't improve with
more volume, consider a category-specific prompt tweak (e.g. an extra worked example
showing a trigger that updates a *related* record correctly, since that's the
recurring failure shape).

## Larger generation run — final dataset (321 examples)

**Context:** Ran `python src/generate_data.py --per-category 30` (v3 prompt), adding
a 4th raw file (237 examples, 3 failed to parse). Re-ran `build_dataset.py` over all
4 raw files (~322 raw examples total).

**Result:**

| category | raw | invalid (dropped) | kept |
|---|---|---|---|
| apex_trigger | 45 | 24 (53%) | 21 |
| apex_async | 45 | 14 (31%) | 31 |
| apex_class | 45 | 4 | 40 |
| apex_test | 43 | 2 | 41 |
| best_practices | 44 | 0 (3 dup) | 41 |
| lwc | 45 | 0 (1 dup) | 44 |
| soql | 45 | 0 | 44 |
| validation_rule | 44 | 0 (1 dup) | 43 |

305 generated + 16 gold = **321 total**, split 275 / 31 / 15 (train/val/test).

**Observation:** `apex_trigger`'s discard rate improved from 67% to 53% with more
samples (more variety = more chances to land on a working pattern), but it's still
the weakest category by a wide margin — roughly half of generated triggers still have
SOQL/DML-in-loop or invented syntax. `apex_async` improved from 40% to 31%. Both
remain consistent with the root cause identified earlier: tasks that require touching
a *related* object (trigger → parent record, async job → query + update) are where
the 3B model is least reliable.

**Decision:** 321 examples is a reasonable size for a LoRA fine-tune on a 3B model —
moving on to the fine-tuning notebook. `apex_trigger` (21 examples) is thin but
workable; if eval shows it's a weak spot post-fine-tuning, that's useful signal for
the writeup ("even after fine-tuning, X remained hard — here's why").

## QLoRA training

Built `notebooks/02_train_qlora.ipynb`, following the same Colab pattern as
`01_smoke_test.ipynb` (HF_TOKEN via Colab secret, `MODEL_ID =
"meta-llama/Llama-3.2-3B-Instruct"`, `BitsAndBytesConfig` 4-bit). I uploaded
`train.jsonl` and `val.jsonl` from `data/processed/` to the Colab session and ran the
notebook on a T4 GPU.

**Design notes:**
- All LoRA/training hyperparameters (rank=16, alpha=32, dropout=0.05, target modules =
  all 7 attention/MLP projections, 3 epochs, batch size 2 with grad accumulation 4,
  lr 2e-4, cosine schedule) are pulled into a single "Config" cell so they're easy to
  tweak without touching the rest of the notebook.
- Pinned `transformers==4.44.2`, `peft==0.12.0`, `trl==0.9.6`, `accelerate==0.33.0`,
  `bitsandbytes==0.43.3`, `datasets==2.20.0` instead of `pip install -U`, since newer
  trl releases (0.12+) changed the `SFTTrainer`/`SFTConfig` API (moved
  `max_seq_length` and `dataset_text_field`/`formatting_func` into a separate
  `SFTConfig`, deprecated passing `tokenizer=` directly). Pinning avoids debugging an
  API mismatch on a one-shot Colab run.
- Used `evaluation_strategy="epoch"` (not `eval_strategy`) — the `eval_strategy` alias
  was only added in later transformers versions than the pinned 4.44.2.
- Formatting uses `tokenizer.apply_chat_template` on `[{"role": "user", ...},
  {"role": "assistant", ...}]` pairs built from `instruction` (+ `input` if present)
  and `output`, passed to `SFTTrainer` via `formatting_func` (not pre-tokenized) so
  the chat template handles the Llama-3.2 special tokens correctly.
- `load_best_model_at_end=True` / `metric_for_best_model="eval_loss"` so the saved
  adapter is the best-eval-loss checkpoint, not necessarily the last epoch — useful
  given only 31 val examples (eval loss could be noisy).

**Results:**
- Model load: 39.7s, 2.26GB GPU memory (well under T4's 16GB)
- LoRA: 24.3M / 3.24B trainable params (0.75%), r=16, alpha=32, dropout=0.05, all 7
  attention/MLP projection modules
- Training time: 54.0 min for 102 steps (3 epochs x 275 examples, effective batch
  size 8 = batch 2 x grad accum 4)
- Final average train loss: 0.6445

| Epoch | Training Loss | Validation Loss |
|---|---|---|
| 1 | 0.760 | 0.7590 |
| 2 | 0.362 | 0.6362 |
| 3 | (~0.34-0.39, see step log) | 0.6350 |

Full step-level training loss: 1.7843 -> 1.3326 -> 0.9477 -> 0.8013 -> 0.7485 -> 0.7602
-> 0.7651 -> 0.5844 -> 0.5839 -> 0.518 -> 0.5164 -> 0.5178 -> 0.4782 -> 0.4621 -> 0.3816
-> 0.3424 -> 0.3769 -> 0.3877 -> 0.3371 -> 0.3619 (steps 5-100, every 5 steps).

**Observation:** Training loss dropped steadily and smoothly throughout (1.78 ->
~0.35). Validation loss improved substantially from epoch 1 -> 2 (0.759 -> 0.636),
then nearly flattened epoch 2 -> 3 (0.636 -> 0.635) while training loss kept dropping
(0.46 -> 0.34) — a mild early signal of incipient overfitting by epoch 3, though not
severe. `load_best_model_at_end=True` selected epoch 3's checkpoint (marginally
lowest eval loss, 0.6350 vs 0.6362).

**Decision:** 3 epochs was a reasonable choice for this dataset size (275 examples) —
the flattening validation curve suggests a 4th epoch would yield diminishing or
negative returns. The adapter was saved to `models/lora-adapter/` (97MB
`adapter_model.safetensors`, r=16/alpha=32/dropout=0.05/7 target modules, base =
Llama-3.2-3B-Instruct, confirmed via `adapter_config.json`), zipped, and downloaded
from Colab. It's gitignored (binary, large).

## Building the evaluation harness

This section covers the design of `notebooks/03_eval.ipynb`, the before/after
evaluation harness, and the resulting scores.

**Context:** Built `notebooks/03_eval.ipynb`, following the load/inference pattern
from `01_smoke_test.ipynb` and the version pins from `02_train_qlora.ipynb`.

**Design notes:**
- **Prompt set (25 total):** 15 held-out `data/processed/test.jsonl` examples (never
  used in training) + 8 `data/baseline_prompts.json` (1 per category) + 2
  general-knowledge sanity prompts ("What is the capital of France?", "Write a haiku
  about autumn") added directly in the notebook to check for catastrophic forgetting.
- **Single model load, two passes:** rather than loading the 4-bit base model twice,
  the notebook loads it once, runs all 25 prompts ("before"), then wraps the same
  in-memory model with `PeftModel.from_pretrained(model, "lora-adapter")` and runs all
  25 prompts again ("after"). This halves model-load time/VRAM churn vs. two separate
  sessions.
- **Greedy decoding (`do_sample=False`):** unlike `01_smoke_test.ipynb`'s sampling
  (`temperature=0.7, top_p=0.9`), eval uses greedy decoding so base-vs-fine-tuned
  responses are reproducible and differences are attributable to the adapter, not
  sampling noise.
- **Adapter delivery:** the adapter was already zipped locally at
  `models/lora-adapter.zip` (92MB). Re-uploading this zip to the Colab session was
  simpler for a one-off eval run than pushing to a private Hugging Face Hub repo
  (which could be worth revisiting for a future iteration).
- **Output:** `comparison.json` with one entry per prompt —
  `{id, category, instruction, expected_output, base_model_response,
  finetuned_model_response, gen times}`. `expected_output` is the gold `output` from
  `test.jsonl` where available, `null` for baseline/sanity prompts (no fixed answer).
- **Version pins:** reused the training notebook's pins (`transformers==4.44.2`,
  `accelerate==0.33.0`, `bitsandbytes>=0.45.0`, `peft==0.12.0`, `sentencepiece`),
  dropped `trl`/`datasets` (not needed for inference-only eval).
- **Scoring approach:** each response was scored 1-5 on correctness, Salesforce
  convention adherence, and completeness, for all 25 prompts x 2 models, once
  `comparison.json` was copied into `eval/results/`. `apex_trigger`/`apex_async`
  (the known weak categories from the dataset-building stage) were spot-checked
  manually given they were flagged as weak spots earlier.

## Running the evaluation

I ran `03_eval.ipynb` on Colab (T4 GPU), uploading `models/lora-adapter/lora-adapter.zip`,
`data/processed/test.jsonl` (not used during training), and `data/baseline_prompts.json`.

**Run results:**
- Hit the same `numpy.dtype size changed` restart issue seen during training, right
  at the base-model-load cell (`Runtime > Restart session`, skip the pip-install
  cell, re-run imports/config/prompt-loading, then the load cell succeeded).
- Base model loaded in 34.6s, 2.26 GB GPU memory allocated.
- Base pass (25 prompts, greedy, up to 768 new tokens): **17.0 minutes**.
- Adapter loaded via `PeftModel.from_pretrained` on the same in-memory model — no
  issues.
- Fine-tuned pass (same 25 prompts): **9.8 minutes** — roughly 1.7x faster than base.
- All 25/25 prompts completed in both passes (no crashes/timeouts).
- `comparison.json` (96KB) downloaded from Colab and copied into `eval/results/`.

**Scoring results** (full detail in [`eval/results/summary.md`](eval/results/summary.md)):
- Scored all 23 task-specific prompts (15 held-out + 8 baseline) on correctness,
  Salesforce convention adherence, and completeness (1-5 each), plus checked the 2
  general-knowledge sanity prompts for catastrophic forgetting.
- **Overall averages:** base 1.68/5, fine-tuned 2.93/5 across the three dimensions.
- **Win/tie/loss:** 16 wins / 6 ties / 1 loss for the fine-tuned model.
- **apex_trigger** (a known weak category from earlier dataset iterations): base avg
  1.83 -> fine-tuned avg 3.83. Base used `after update` triggers that can't safely
  mutate fields in that context; fine-tuned correctly used `before update` with
  `Trigger.oldMap` comparisons on both prompts.
- **apex_async** (also a known weak category): base avg 1.56 -> fine-tuned avg 2.89.
  Fine-tuned correctly implemented `Schedulable`/`Database.Batchable` with
  `start`/`execute`/`finish`; the one loss (`apex-queueable-callout`) was both models
  hallucinating HTTP callout APIs, with fine-tuned additionally using `insert` instead
  of `update`.
- **Notable failure mode eliminated:** the base model got stuck in repetition loops
  (generating the same line hundreds of times until the 768-token cap) on 4/23
  prompts, producing no usable output at all. The fine-tuned model never did this —
  every response was a complete, on-topic attempt even when buggy. This is also the
  main driver of the 17.0 -> 9.8 minute speedup.
- **Catastrophic forgetting check:** no regression. Both models correctly answered
  "What is the capital of France?" and both produced valid on-theme 5-7-5 haikus about
  autumn.
- **Remaining weak spots (ties):** `with sharing`/`without sharing` explanation (both
  models described it backwards), and harder SOQL aggregation queries with
  multi-condition `GROUP BY`/`ORDER BY`. Good candidates for future training-data
  expansion.

For the final summary table and headline numbers, see
[README.md → Results](README.md#results) and
[`eval/results/summary.md`](eval/results/summary.md).
