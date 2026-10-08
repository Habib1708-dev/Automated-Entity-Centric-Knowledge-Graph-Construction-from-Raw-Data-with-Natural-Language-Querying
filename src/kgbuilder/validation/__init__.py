"""Validation and evaluation of the finished graph.

validator.py runs the check families in checks/ (no gold data needed); gold.py holds the gold file
models and matching; evaluate.py scores the graph against the gold (exact match) and, through judge.py,
against the judge's verdicts (by meaning). report.py holds the shared result models. sentences.py,
coverage_sheet.py and coverage.py estimate how much of what the text states the graph holds, from a
judged sample of sentences (R68); interval.py gives every rate from a sample its Wilson interval.
qa_gold.py holds the question-answer gold file and its checks against the corpus, and qa.py scores a
system's answers to it (R70); qa_records.py computes the answers of record questions from the source
files with DuckDB, and paired.py compares two systems question by question (R73). target_gold.py holds
the anchor-graph target gold: for each QA question, the names it starts from and what they reach (R89).
retrieval_scores.py scores retrieval without the reader: evidence and seed recall within budgets, and
latency (R117).
"""
