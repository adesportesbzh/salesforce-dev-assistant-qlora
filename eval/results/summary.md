# Evaluation Summary: Base vs. Fine-Tuned

Scoring of `comparison.json` (25 prompts: 15 held-out test examples, 8 baseline prompts, 2
general-knowledge sanity prompts). Each task-specific response was scored 1–5 on three
dimensions per the PLAN.md rubric:

- **Correctness** — does the code/explanation actually do what was asked, and would it work?
- **Convention** — does it follow idiomatic Salesforce/Apex/SOQL/LWC patterns and naming?
- **Completeness** — does it fully address all parts of the instruction?

Scoring was done by Claude against this rubric. Anthony, please spot-check the
`apex_trigger` and `apex_async` rows below (flagged in DEVLOG as weak categories after
Epic B) — these are marked **[SPOT-CHECK]**.

## Overall results (23 task-specific prompts)

| | Correctness (avg) | Convention (avg) | Completeness (avg) | Overall (avg) |
|---|---|---|---|---|
| Base model | 1.35 | 1.61 | 2.09 | 1.68 |
| Fine-tuned model | 2.57 | 3.22 | 3.00 | 2.93 |

**Win / tie / loss (fine-tuned vs. base, by overall impression):** 16 wins / 6 ties / 1 loss.

The single loss was `apex-queueable-callout` — both models invented non-existent
Salesforce APIs for the HTTP callout, but the base model's invented names were slightly
more plausible and it didn't have the fine-tuned model's `insert`-instead-of-`update` bug.

## Category breakdown (flagged categories)

| Category | Prompts | Base avg | Fine-tuned avg | Notes |
|---|---|---|---|---|
| `apex_trigger` **[SPOT-CHECK]** | `apex_trigger-clean-017`, `apex-trigger-basic` | 1.83 | 3.83 | Base used `after update` (can't safely mutate fields there) and invented field/relationship structures. Fine-tuned used `before update` with `Trigger.oldMap` comparisons — the correct pattern — on both prompts. |
| `apex_async` **[SPOT-CHECK]** | `apex-async-archive-opps-job-010`, `apex_async-clean-006`, `apex-queueable-callout` | 1.56 | 2.89 | Fine-tuned correctly implemented `Schedulable` and `Database.Batchable` interfaces with `start`/`execute`/`finish`; base produced a non-functional repetition loop for the Batchable prompt. The Queueable callout prompt was a loss for fine-tuned (see above) — both models hallucinated HTTP callout APIs. |

## Qualitative observations

**Base model got stuck in repetition loops on 4 of 23 prompts** (`apex_test-clean-022`,
`apex_async-clean-006`, `best_practices-clean-034`, `apex_test-clean-009`) — it would
generate the same line (e.g., `"Test User"`, `"Anytown, USA"`, `getApexMethodIdForBatch()`)
hundreds of times until hitting `MAX_NEW_TOKENS=768`, producing no usable code at all. The
fine-tuned model never did this — every response was a complete, on-topic attempt, even
when the logic had bugs.

**Fine-tuned responses were both better and faster.** Across all 25 prompts, the base
model took 17.0 minutes total vs. 9.8 minutes for the fine-tuned model — roughly 1.7x
faster. This lines up with the repetition-loop issue above (base often ran to the full
768-token cap) and with fine-tuned responses generally being more concise and to the point
(e.g., often just the requested code with little to no surrounding prose).

**Format adherence improved.** For `validation_rule` prompts, the fine-tuned model
consistently used the `Formula (Error Condition Formula): ... Error Message: ... Error
Location: ...` structure that matches the training data, even when the formula logic
itself was still flawed. The base model instead produced free-form prose with invented
formula syntax.

**SOQL relationship syntax improved.** On `soql-relationship`, the fine-tuned model used
correct dot-notation (`Account.Name`, `Account.Industry`) for traversing relationships —
the base model used SQL-style `INNER JOIN ... ON`, which isn't valid SOQL at all.

## Ties — both models struggled equally

`best-practices-sharing-keywords-015` (with/without sharing), `soql-clean-003`,
`apex_class-clean-020`, `validation_rule-clean-003`, `apex-class-map`, and
`validation-rule` were scored as ties — both models got the core logic wrong or missed
part of the requirement, though the fine-tuned responses were usually more concise. The
`with sharing` / `without sharing` explanation is a notable miss for both models: both
described the keywords backwards (neither correctly framed it as record-level sharing-rule
enforcement vs. system context).

## Catastrophic forgetting check (2 general-knowledge sanity prompts)

| Prompt | Base response | Fine-tuned response | Regression? |
|---|---|---|---|
| "What is the capital of France?" | "The capital of France is Paris." | "Paris." | No — both correct. Fine-tuned is just terser. |
| "Write a haiku about autumn." | "Golden leaves fall slow / Crisp air whispers autumn's end / Nature's final dance" | "Golden leaves descend / Crisp air whispers summer's end / Nature's fleeting dance" | No — both valid 5-7-5 haikus on theme. Fine-tuned's "summer's end" is a minor thematic quirk but still autumn-adjacent (end of summer = start of autumn). |

**No evidence of catastrophic forgetting** — general-knowledge ability is preserved after
fine-tuning on the Apex/SOQL/LWC dataset.

## Bottom line

The fine-tuned model is a clear improvement over the base model on the target task: higher
scores across all three rubric dimensions (overall average 2.93 vs. 1.68), a 16/6/1
win/tie/loss record, elimination of the repetition-loop failure mode seen 4 times in the
base model, faster generation, and no measurable regression on general knowledge. Several
ties show there's still room to improve on harder SOQL aggregation queries and conceptual
explanations (`with sharing`/`without sharing`), which would be good candidates for a
future training-data expansion.
