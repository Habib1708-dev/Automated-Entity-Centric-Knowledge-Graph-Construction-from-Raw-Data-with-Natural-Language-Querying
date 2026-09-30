"""Validation and evaluation of the finished graph.

validator.py runs the check families in checks/ (no gold data needed); gold.py holds the gold file
models and matching; evaluate.py scores the graph against the gold (exact match) and, through judge.py,
against the judge's verdicts (by meaning). report.py holds the shared result models. sentences.py,
coverage_sheet.py and coverage.py estimate how much of what the text states the graph holds, from a
judged sample of sentences (R68); interval.py gives every rate from a sample its Wilson interval.
qa_gold.py holds the question-answer gold file and its checks against the corpus, and qa.py scores a
system's answers to it (R70).
"""
