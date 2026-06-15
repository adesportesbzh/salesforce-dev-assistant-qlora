"""
B3 — Heuristic structural validation for generated examples.

llama3.2:3B reliably produces text in the right INSTRUCTION/INPUT/OUTPUT *format*
(B2 already filters parse failures), but the *code* in `output` often isn't valid
Apex — see architecture/DEVLOG.md for the specific failure modes found in B2 v1-v3:

  - SOQL queries or DML statements (insert/update/delete/upsert/merge) placed inside
    a "for" loop (governor-limit anti-pattern, and the most persistent failure mode)
  - Invented syntax that isn't real Apex (setOf(...), nested "trigger" declarations,
    SQL-style "insert X values (...)", reassigning Trigger.new/Trigger.oldMap, the
    non-existent ApexException class)
  - Spaces inside what should be a single dotted field reference
    (e.g. "so.Total Items Shipped" instead of "so.Total_Items_Shipped__c")
  - Unbalanced braces/parens/brackets

This script can't fully compile Apex (no Salesforce org / Apex compiler available),
but these checks catch a large share of the issues we've actually observed.

Usage:
    python src/validate_examples.py data/raw/generated.jsonl
    python src/validate_examples.py data/raw/generated.jsonl --write-clean data/raw/generated_clean.jsonl
    python src/validate_examples.py data/raw/generated.jsonl --write-report data/raw/validation_report.json
"""

import argparse
import json
import re
from pathlib import Path

# Categories whose `output` is expected to be compilable Apex (class/trigger body).
# soql (bare query), validation_rule (formula language), lwc (JS/HTML + Apex
# fragment), and best_practices (prose + illustrative snippets) have different
# grammars and/or aren't complete units, so the Apex-specific checks below don't
# apply cleanly to them.
APEX_CATEGORIES = {"apex_trigger", "apex_class", "apex_async", "apex_test"}

DML_KEYWORDS = r"(?:insert|update|delete|upsert|merge)"

KNOWN_INVALID_PATTERNS = [
    (r"\bsetOf\s*\(", "uses non-existent setOf(...) function"),
    (r"Trigger\.(new|old)\s*=(?!=)", "illegally reassigns Trigger.new/Trigger.old"),
    (r"Trigger\.(oldMap|newMap)\.clear\s*\(", "illegally calls .clear() on Trigger.oldMap/newMap"),
    (r"\bApexException\b", "references non-existent ApexException class"),
    (rf"\b{DML_KEYWORDS}\s+\w+\s+\w+\s*\([^)]*\)\s*values\s*\(", "SQL-style 'INSERT ... VALUES (...)' is not valid Apex DML"),
    (r"\btrigger\s+\w+\s*:", "looks like a nested/invalid trigger declaration"),
]


def check_balanced_delimiters(code):
    """Each delimiter type must be self-balanced and never go negative."""
    issues = []
    for open_ch, close_ch, name in (("{", "}", "braces"), ("(", ")", "parens"), ("[", "]", "brackets")):
        depth = 0
        went_negative = False
        for ch in code:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth < 0:
                    went_negative = True
        if depth != 0 or went_negative:
            issues.append(f"unbalanced {name} (final depth {depth}, went negative: {went_negative})")
    return issues


def find_for_loop_body_spans(code):
    """Return a list of (start, end) character spans covering the body of every
    'for (...) { ... }' loop in code, including nested loops."""
    spans = []
    for m in re.finditer(r"\bfor\s*\(", code):
        # Find the matching ')' for this 'for ('.
        i = m.end()
        depth = 1
        while i < len(code) and depth > 0:
            if code[i] == "(":
                depth += 1
            elif code[i] == ")":
                depth -= 1
            i += 1
        if depth != 0:
            continue  # unbalanced, skip — caught by check_balanced_delimiters

        # Skip whitespace to find the opening '{' of the loop body.
        j = i
        while j < len(code) and code[j] in " \t\r\n":
            j += 1
        if j >= len(code) or code[j] != "{":
            continue  # single-statement for loop body (no braces) — ignore

        body_start = j + 1
        depth = 1
        k = body_start
        while k < len(code) and depth > 0:
            if code[k] == "{":
                depth += 1
            elif code[k] == "}":
                depth -= 1
            k += 1
        if depth != 0:
            continue

        spans.append((body_start, k - 1))
    return spans


def check_soql_or_dml_in_loop(code):
    issues = []
    for start, end in find_for_loop_body_spans(code):
        body = code[start:end]
        if re.search(r"\[\s*SELECT\b", body, re.IGNORECASE):
            issues.append("SOQL query ([SELECT ...]) found inside a 'for' loop")
        # DML statement starting a new statement (preceded by ; { or start-of-body)
        if re.search(rf"(?:^|[;{{}}])\s*{DML_KEYWORDS}\s+\w", body, re.IGNORECASE | re.MULTILINE):
            issues.append("DML statement (insert/update/delete/upsert/merge) found inside a 'for' loop")
    return issues


def check_known_invalid_patterns(code):
    issues = []
    for pattern, message in KNOWN_INVALID_PATTERNS:
        if re.search(pattern, code):
            issues.append(message)
    return issues


# Words that legitimately follow a dotted reference in SOQL/Apex (don't treat as
# part of an invalid "spaced field name").
_SOQL_KEYWORDS = (
    r"AND|OR|NOT|IN|LIKE|INCLUDES|EXCLUDES|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|"
    r"ASC|DESC|NULL|NULLS|FIRST|LAST|FROM|WHERE|SELECT|AS"
)


def check_spaced_field_reference(code):
    """Catch patterns like 'so.Total Items Shipped = 0;' — a Title-Case dotted
    reference followed by 2+ extra capitalized words before an operator. Requires
    every extra word to start with a capital letter (to avoid matching valid
    declarations like 'Messaging.SingleEmailMessage mail = ...') and excludes SOQL
    keywords (to avoid matching 'WHERE x.Id AND StageName = ...')."""
    issues = []
    pattern = rf"\.[A-Za-z_]\w*(?:\s+(?!(?:{_SOQL_KEYWORDS})\b)[A-Z]\w*){{2,4}}\s*(?:=(?!=)|;)"
    if re.search(pattern, code):
        issues.append("dotted field/property reference contains spaces (likely an invalid custom field name)")
    return issues


def validate_example(example):
    """Return a list of issue strings (empty list = passes all checks)."""
    output = example.get("output", "")
    category = example.get("category", "")
    issues = []

    issues += check_balanced_delimiters(output)

    if category in APEX_CATEGORIES:
        issues += check_soql_or_dml_in_loop(output)
        issues += check_known_invalid_patterns(output)
        issues += check_spaced_field_reference(output)

    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSONL file to validate (e.g. data/raw/generated.jsonl)")
    parser.add_argument("--write-clean", type=Path, help="Write examples with no issues to this JSONL file")
    parser.add_argument("--write-report", type=Path, help="Write a JSON report of all issues to this file")
    args = parser.parse_args()

    examples = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))

    report = []
    clean = []
    by_category = {}

    for ex in examples:
        issues = validate_example(ex)
        cat = ex.get("category", "unknown")
        stats = by_category.setdefault(cat, {"total": 0, "clean": 0})
        stats["total"] += 1
        if issues:
            report.append({"id": ex.get("id"), "category": cat, "issues": issues})
        else:
            stats["clean"] += 1
            clean.append(ex)

    print(f"Validated {len(examples)} examples from {args.input}\n")
    print(f"{'category':<18} {'clean':>6} / {'total':>6}")
    for cat, stats in sorted(by_category.items()):
        print(f"{cat:<18} {stats['clean']:>6} / {stats['total']:>6}")

    total_clean = sum(s["clean"] for s in by_category.values())
    total = sum(s["total"] for s in by_category.values())
    print(f"\n{'TOTAL':<18} {total_clean:>6} / {total:>6} ({100 * total_clean / total:.0f}% clean)")

    if report:
        print(f"\n{len(report)} examples flagged:")
        for r in report:
            print(f"  {r['id']} ({r['category']}):")
            for issue in r["issues"]:
                print(f"    - {issue}")

    if args.write_clean:
        args.write_clean.parent.mkdir(parents=True, exist_ok=True)
        with open(args.write_clean, "w") as f:
            for ex in clean:
                f.write(json.dumps(ex) + "\n")
        print(f"\nWrote {len(clean)} clean examples to {args.write_clean}")

    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        with open(args.write_report, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Wrote validation report to {args.write_report}")


if __name__ == "__main__":
    main()
