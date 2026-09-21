"""Shared kernel: small, dependency-free helpers used by every other package.

Role in the pipeline: none of its own; it is the bottom of the dependency graph.
Design: nothing in here may import from the rest of kgbuilder, so any package can import it safely.
"""
