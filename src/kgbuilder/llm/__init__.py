"""LLM access: the protocols the pipeline depends on, and the adapters that implement them.

Feature code imports only `kgbuilder.llm.base`. The adapters (`gemini`, `cache`) are wired together by
the composition root in `cli.py`.
"""
