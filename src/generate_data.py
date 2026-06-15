"""
B2 — Synthetic training data generation.

Uses a local Ollama model (default: llama3.2) to generate instruction/output pairs
for fine-tuning, across the 8 categories defined in data/SCHEMA.md. The gold
examples in data/gold/gold_examples.jsonl are used as few-shot style guides.

Requires: `ollama` running locally with the target model pulled
    ollama pull llama3.2

Usage:
    python src/generate_data.py --per-category 25
    python src/generate_data.py --per-category 50 --model llama3.2 --temperature 0.6
    python src/generate_data.py --category apex_trigger --per-category 10

Output: appends to data/raw/generated.jsonl (creates it if missing).
"""

import argparse
import json
import random
import re
from pathlib import Path

import ollama

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data" / "gold" / "gold_examples.jsonl"
OUTPUT_PATH = ROOT / "data" / "raw" / "generated.jsonl"

CATEGORY_DESCRIPTIONS = {
    "apex_trigger": "An Apex trigger on a standard or custom Salesforce object.",
    "soql": "A SOQL query (relationships, aggregates, filters, etc.).",
    "apex_class": "A standalone Apex class (utility, service, pattern implementation).",
    "validation_rule": "A Salesforce validation rule formula.",
    "apex_async": "Asynchronous Apex (Queueable, Batchable, Schedulable, or @future).",
    "lwc": "A Lightning Web Component, optionally paired with an Apex controller method.",
    "apex_test": "An Apex @isTest test class.",
    "best_practices": "An explanation of a Salesforce/Apex best practice, often with a code example or refactor.",
}

# Seed topics per category — used to steer variety. The model is asked to riff on
# one of these, not copy it verbatim.
TOPICS = {
    "apex_trigger": [
        "a trigger on Contact (before delete) that blocks deletion if the contact has open Cases",
        "a trigger on Case (before insert) that auto-assigns Priority based on a custom Severity__c field",
        "a trigger on a custom object Invoice__c (after update) that recalculates a rollup total on the parent Account",
        "a trigger on Lead (before update) that clears converted-related fields if Status changes back to an open value",
        "a trigger on Task (after insert) that updates a Last_Activity_Date__c field on the related Account or Contact",
        "a trigger on Opportunity (after update) that creates a follow-up Task when StageName changes to 'Closed Lost'",
        "a trigger on Contact (before insert) that defaults the MailingCountry from the parent Account's BillingCountry",
        "a trigger on Order (after update) that prevents activation if any OrderItem has a negative quantity",
        "a trigger on Account (after insert) that creates a default Contact record for the new account",
        "a trigger on Campaign Member (after insert) that increments a custom counter field on the related Campaign",
    ],
    "soql": [
        "a query that returns Accounts with no related Opportunities (using a NOT IN subquery)",
        "a query using a parent-to-child relationship subquery to get Accounts with their open Cases",
        "a query that filters on a date literal like LAST_N_DAYS or THIS_FISCAL_QUARTER",
        "a query that uses GROUP BY and HAVING to find Owners with more than 10 open Opportunities",
        "a query with WITH SECURITY_ENFORCED to respect field- and object-level security",
        "a query that retrieves polymorphic lookup fields (e.g., the What field on a Task)",
        "a query that uses FOR UPDATE to lock records before an update in Apex",
        "a query that returns the top 3 Opportunities by Amount per Account using a nested query",
        "a query that filters Contacts by a multi-select picklist field using INCLUDES()",
        "a query that retrieves grandparent field values via a multi-level relationship (e.g., Contact.Account.Owner.Name)",
    ],
    "apex_class": [
        "a service class that calculates shipping cost based on order weight tiers",
        "a wrapper class used to combine data from two different objects for display in a UI",
        "a class implementing the Strategy pattern for different discount calculation strategies",
        "a class that wraps an HTTP callout to a REST API and parses the JSON response into typed Apex objects",
        "a utility class with a method to recursively flatten a hierarchy of Account records into a list",
        "a class implementing a simple Factory pattern to create different types of Task records based on an enum",
        "a class that converts a List<SObject> into a CSV string for export",
        "a class that validates a custom object's fields against a set of business rules before insert",
        "a class with a method that merges duplicate Contact records based on matching email addresses",
        "a class that exposes a method to calculate business days between two dates, excluding weekends",
    ],
    "validation_rule": [
        "a rule that requires a Reason__c field to be populated when an Opportunity is marked Closed Lost",
        "a rule that prevents a Case from being closed if a required custom checkbox 'Resolution_Confirmed__c' is false",
        "a rule that enforces a specific date format relationship: End_Date__c must be after Start_Date__c",
        "a rule that restricts editing a custom 'Locked__c' record unless the user is in a specific permission set (using $Permission)",
        "a rule that requires at least one of two optional fields to be filled in before save",
        "a rule that prevents setting a discount percent field above 50 unless StageName is 'Negotiation'",
        "a rule that ensures a picklist field 'Region__c' is consistent with the value of a related Country__c field",
        "a rule that prevents back-dating CreatedDate-like custom date fields to before the current fiscal year",
    ],
    "apex_async": [
        "a Queueable class that chains itself to process records in batches of 200 until none remain",
        "a Batchable class with Database.Stateful that tracks a running total across batches and emails a summary in finish()",
        "a Schedulable class that runs hourly during business hours only, re-scheduling itself for the next valid hour",
        "a @future method that performs a callout to update an external system when an Account's status changes",
        "a Queueable class that publishes a Platform Event after processing a list of records",
        "a Batchable class that uses Database.QueryLocator with a complex WHERE clause to clean up stale custom object records",
        "a Schedulable class that triggers a Batchable job and passes a configurable batch size",
        "a Queueable class with error handling that logs failures to a custom Error_Log__c object instead of throwing",
    ],
    "lwc": [
        "a component using lightning-record-form to create a new Contact related to the current Account record page",
        "a component that uses the Lightning Message Service to broadcast a selected record Id between sibling components",
        "a component with a lightning-datatable showing related list records with pagination (Load More button)",
        "a component that displays a progress bar based on a percentage field, updating live via @wire on record change",
        "a component with a modal dialog (using LightningModal) to confirm a destructive action before calling Apex",
        "a parent-child component pair where the parent passes a list of records via a public @api property",
        "a component that debounces a search input before calling an imperative Apex search method",
        "a component that conditionally shows/hides sections based on the value of a picklist field, using getters",
    ],
    "apex_test": [
        "a test class with a @testSetup method that creates shared test data for multiple test methods",
        "a test class that uses Test.setMock and HttpCalloutMock to test a class that performs an HTTP callout",
        "a test class that verifies a validation rule fires by asserting a DmlException is thrown with the expected message",
        "a test class that creates 200 records to verify a trigger is bulk-safe and stays within governor limits",
        "a test class that uses System.runAs to test behavior under a specific user profile or permission set",
        "a test class for a Queueable class, using Test.startTest/stopTest to force synchronous execution",
        "a test class that verifies a Schedulable class can be scheduled and runs via Test.startTest/stopTest with a CRON expression",
        "a test class that checks both the positive case and a negative/edge case (e.g., empty list input) for a utility method",
    ],
    "best_practices": [
        "explain why hardcoded Ids should be avoided in Apex and how to handle record-type-specific logic instead",
        "explain the trigger-per-object pattern (one trigger, handler class) and why it's preferred over multiple triggers per object",
        "explain the difference between @future, Queueable, and Batchable Apex and when to use each",
        "explain how to check CRUD and field-level security (FLS) in Apex before performing DML, and show an example using Schema describe or WITH SECURITY_ENFORCED",
        "explain why SOQL queries and DML statements inside loops are problematic and how to detect this with static analysis tools",
        "explain the difference between Database.insert(records, false) and a plain insert statement, and when partial success handling is useful",
        "explain how to avoid recursive trigger execution using a static boolean flag pattern",
        "explain the governor limit implications of using nested for loops over large SOQL result sets, with an example refactor",
    ],
}

SYSTEM_PROMPT = """You are a senior Salesforce developer creating training data to fine-tune a \
coding assistant. You will be given a category, a topic idea, and 1-2 example tasks for \
style reference. Generate ONE NEW example that is different from the reference examples \
(different object names, fields, or scenario details), but in the same style and quality.

The code you write must be valid Apex/SOQL/LWC that would actually compile. Before \
responding, double-check against these common pitfalls:
- Custom object and field API names must end in "__c" and must NEVER contain spaces \
(e.g., Total_Items_Shipped__c, not "Total Items Shipped"). Standard field names \
(Name, Amount, StageName, etc.) have no "__c" suffix and no spaces either.
- In a trigger, to block a save use record.addError('message') — Apex has no built-in \
"ApexException" class. Only throw exceptions you define yourself (e.g., a class that \
extends Exception) or built-in ones like DmlException.
- Never put a SOQL query or DML statement inside a "for" loop — query/collect data \
before the loop, then iterate over the in-memory results.
- Trigger.oldMap and Trigger.newMap are Maps keyed by record Id. ".get(id)" returns a \
single record, not a collection — never use it directly as a "for" loop target.
- Every variable must be declared before use and stay within its scope (don't reference \
a loop variable outside that loop).
- All braces {}, parentheses (), and brackets [] must be balanced and properly nested.

Here are real mistakes from earlier generations of this dataset, with corrections. Do not \
repeat these mistakes:

MISTAKE: Using SQL-style INSERT...VALUES syntax, which does not exist in Apex:
    insert AccountBlock record (Id Id, Boolean Blocked) values ([: blockedAccountIds]);
CORRECTED: Apex DML inserts sObject instances created with the `new` keyword:
    AccountBlock__c block = new AccountBlock__c(Account__c = acc.Id, Blocked__c = true);
    insert block;

MISTAKE: Querying inside a "for" loop (hits governor limits):
    for (Account acc : Trigger.new) {
        for (Opportunity opp : [SELECT Id FROM Opportunity WHERE AccountId = :acc.Id]) { ... }
    }
CORRECTED: Collect Ids first, run ONE query before the loop, then look up results in memory:
    Set<Id> accountIds = new Set<Id>();
    for (Account acc : Trigger.new) { accountIds.add(acc.Id); }
    List<Opportunity> opps = [SELECT Id, AccountId FROM Opportunity WHERE AccountId IN :accountIds];

MISTAKE: A space inside a custom field API name:
    so.Total Items Shipped = 0;
CORRECTED:
    so.Total_Items_Shipped__c = 0;

MISTAKE: Treating Trigger.oldMap.get(id) as a collection to loop over:
    for (Account acc : Trigger.oldMap.get(acc.Id)) { ... }
CORRECTED: get(id) returns a single record — use it directly, don't loop over it:
    Account oldAcc = (Account) Trigger.oldMap.get(acc.Id);

Respond using EXACTLY this format, with no extra commentary before or after:

INSTRUCTION:
<the natural-language task, as a developer would phrase it>

INPUT:
<additional context such as existing code to refactor, or the word NONE if not needed>

OUTPUT:
<the response: code and/or explanation>

END
"""


def load_gold_examples():
    examples = []
    with open(GOLD_PATH) as f:
        for line in f:
            examples.append(json.loads(line))
    return examples


def build_prompt(category, topic, few_shot):
    parts = [f"Category: {category} — {CATEGORY_DESCRIPTIONS[category]}", "", f"Topic idea: {topic}", ""]
    for i, ex in enumerate(few_shot, 1):
        parts.append(f"--- Reference example {i} ---")
        parts.append(f"INSTRUCTION:\n{ex['instruction']}")
        parts.append(f"INPUT:\n{ex['input'] or 'NONE'}")
        parts.append(f"OUTPUT:\n{ex['output']}")
        parts.append("")
    parts.append("Now generate ONE new example for the topic idea above, following the required format.")
    return "\n".join(parts)


def parse_response(text):
    """Parse the delimiter-based format into (instruction, input, output)."""
    pattern = r"INSTRUCTION:\s*(.*?)\s*INPUT:\s*(.*?)\s*OUTPUT:\s*(.*?)\s*(?:END\s*)?$"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None
    instruction, input_, output = (g.strip() for g in match.groups())
    if input_.upper() == "NONE":
        input_ = ""
    if not instruction or not output:
        return None
    return instruction, input_, output


def normalize(text):
    return re.sub(r"\s+", " ", text.strip().lower())


def load_existing_instructions():
    seen = set()
    for path in (GOLD_PATH, OUTPUT_PATH):
        if path.exists():
            with open(path) as f:
                for line in f:
                    try:
                        seen.add(normalize(json.loads(line)["instruction"]))
                    except (json.JSONDecodeError, KeyError):
                        continue
    return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-category", type=int, default=25, help="Examples to generate per category")
    parser.add_argument("--category", choices=list(TOPICS.keys()), help="Generate only this category")
    parser.add_argument("--model", default="llama3.2", help="Ollama model name")
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--max-retries", type=int, default=3, help="Retries per example on parse failure or duplicate")
    parser.add_argument("--few-shot", type=int, default=2, help="Number of gold examples to use as few-shot reference")
    args = parser.parse_args()

    gold = load_gold_examples()
    gold_by_category = {}
    for ex in gold:
        gold_by_category.setdefault(ex["category"], []).append(ex)

    categories = [args.category] if args.category else list(TOPICS.keys())
    seen_instructions = load_existing_instructions()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    total_written = 0
    total_failed = 0

    with open(OUTPUT_PATH, "a") as out:
        for category in categories:
            topics = TOPICS[category]
            few_shot_pool = gold_by_category.get(category, [])

            for i in range(args.per_category):
                topic = topics[i % len(topics)]
                if i >= len(topics):
                    topic += " (use different field/object names than any previous example)"

                written = False
                for attempt in range(args.max_retries):
                    few_shot = random.sample(few_shot_pool, min(args.few_shot, len(few_shot_pool)))
                    prompt = build_prompt(category, topic, few_shot)

                    response = ollama.chat(
                        model=args.model,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        options={"temperature": args.temperature},
                    )
                    text = response["message"]["content"]
                    parsed = parse_response(text)

                    if parsed is None:
                        print(f"[{category}] attempt {attempt + 1}: failed to parse response, retrying")
                        continue

                    instruction, input_, output = parsed
                    norm = normalize(instruction)
                    if norm in seen_instructions:
                        print(f"[{category}] attempt {attempt + 1}: duplicate instruction, retrying")
                        continue

                    seen_instructions.add(norm)
                    record = {
                        "id": f"{category}-gen-{total_written + 1:04d}",
                        "category": category,
                        "instruction": instruction,
                        "input": input_,
                        "output": output,
                    }
                    out.write(json.dumps(record) + "\n")
                    out.flush()
                    total_written += 1
                    written = True
                    print(f"[{category}] {i + 1}/{args.per_category} -> {record['id']}")
                    break

                if not written:
                    total_failed += 1
                    print(f"[{category}] giving up on example {i + 1} after {args.max_retries} attempts")

    print(f"\nDone. Wrote {total_written} examples to {OUTPUT_PATH} ({total_failed} failed).")


if __name__ == "__main__":
    main()
