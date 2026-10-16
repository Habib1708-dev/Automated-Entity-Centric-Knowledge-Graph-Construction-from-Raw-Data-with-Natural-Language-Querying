"""The web interface (plan R221-R224): an HTTP API over the benchmarked datasets and the agent, for the page.

R222: the dataset tabs (datasets.py), each build's graph read offline (knowledge.py), the streamed question
(ask.py, events.py) and the routes (app.py). `kg serve` in cli.py is the composition root that builds the
concrete parts.
Dependencies: web -> pipeline, agent's observer and result models, audit's snapshot, graph's digest, core.
Nothing imports web but cli.py.
"""
