"""The web API's OpenAPI schema with the stream's events (R223), the source the page's TypeScript types are
generated from (`npm run api` in frontend/), so the page and the API cannot drift apart unnoticed.

Role in the pipeline: build time only. `python -m kgbuilder.web.openapi` prints the schema; the committed
copy, frontend/openapi.json, is held equal to it by a test.
Design: FastAPI writes every route's models; the ask route streams server-sent events, which OpenAPI cannot
describe as a response, so the events' discriminated union is added to the components as `StreamEvent`.
Must not: open Neo4j, read settings or build the real parts: the schema needs only the routes, so the app is
made over stand-ins that are never called.
"""

import json
from typing import Any

from pydantic import TypeAdapter

from .app import create_app
from .events import StreamEvent
from .load import LoadEvent


class _NoAsker:
    """An `Asker` for building the schema; the schema never calls it."""

    def loaded_digest(self, any_dataset):  # noqa: ANN001, ANN201 - never called
        raise NotImplementedError

    def ask(self, dataset, question, variant, emit):  # noqa: ANN001, ANN201 - never called
        raise NotImplementedError


def schema() -> dict[str, Any]:  # Any: the OpenAPI document is free-form JSON
    """The API's OpenAPI document, its components holding `StreamEvent`, `LoadEvent` (R222b) and every
    event model."""

    def no_graph(dataset):  # noqa: ANN001, ANN202 - never called
        raise NotImplementedError

    document = create_app([], no_graph, _NoAsker(), []).openapi()
    events = TypeAdapter(StreamEvent).json_schema(ref_template="#/components/schemas/{model}")
    components = document.setdefault("components", {}).setdefault("schemas", {})
    components.update(events.pop("$defs", {}))
    components["StreamEvent"] = events
    loads = TypeAdapter(LoadEvent).json_schema(ref_template="#/components/schemas/{model}")
    components.update(loads.pop("$defs", {}))
    components["LoadEvent"] = loads
    return document


def main() -> None:
    print(json.dumps(schema(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
