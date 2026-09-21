"""Project exception types, so callers can tell expected pipeline failures from bugs.

Role in the pipeline: stages raise these; the CLI turns any `KgBuilderError` into a readable message and
a non-zero exit code instead of a traceback.
"""


class KgBuilderError(Exception):
    """Base class for every failure the pipeline expects and can explain to the user."""


class LLMUnavailableError(KgBuilderError):
    """An LLM call was needed but no provider is configured (for example the API key is missing)."""


class LLMResponseError(KgBuilderError):
    """The LLM kept failing or returned output that does not parse into the requested schema."""


class ProposalRejectedError(KgBuilderError):
    """An LLM proposal (construction plan or text schema) still had open issues after the last round."""

    def __init__(self, what: str, issues: list[str]):
        super().__init__(f"{what} not accepted: " + "; ".join(issues))
        self.issues = issues


class InvalidPlanError(KgBuilderError):
    """A construction plan (possibly edited by hand) does not match the profiled data."""

    def __init__(self, issues: list[str]):
        super().__init__("invalid plan: " + "; ".join(issues))
        self.issues = issues


class MissingInputError(KgBuilderError):
    """A stage was started before the stage that produces its input (for example no out/plan.json)."""
