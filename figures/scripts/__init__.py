"""The code that draws every figure (R129): `style` (palette, encoding, saving), one data module and one chart
module per measured result, one module per diagram set, and `build` (regenerates them all:
`uv run python -m figures.scripts.build`).
Must not: compute a new result. A figure shows numbers a committed record already holds.
"""

__all__: list[str] = []
