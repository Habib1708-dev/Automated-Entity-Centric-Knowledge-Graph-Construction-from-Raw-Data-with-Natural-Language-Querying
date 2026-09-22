"""LLM access: the protocols the pipeline depends on, and the adapters that implement them.

Feature code imports only `kgbuilder.llm.base`. The adapters (`gemini`, `ollama`, `cache`) share the
retry loop in `retry` and are wired together by the composition root in `cli.py`.
"""
