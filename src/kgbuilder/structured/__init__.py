"""Structured path: tables (CSV/JSON files or a PostgreSQL schema) -> profile -> plan -> domain graph.

Order of use: staging (postgres: the database adapter, R114) -> profiler -> proposer (LLM) + plan
(validation) -> importer.
"""
