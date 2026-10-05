"""The planner (R74): the model writes a query plan of fixed primitives for a question; code checks and
runs it.

Role in the pipeline: the first step of the plan systems (systems.py `PlanSystem`), replacing the router and
free text2cypher of R71. The plan is checked by plan.py and run by plan_run.py; a refused plan gets one
retry with the reasons.
Design: one domain-neutral prompt with the graph's schema (read from the graph, records only for records
plus vector RAG) and an example from an invented domain (prompt-engineering skill); the reply is a
`QueryPlan`, validated by pydantic and then by `check_plan`. The model chooses steps and values; it never
writes a query, a direction or an identifier.
Not here: checking or running a plan (plan.py, plan_run.py), the fallbacks (systems.py).
"""

from ..llm.base import LLMClient
from .plan import QueryPlan

# The planner's prompt. Rule by rule, with the failure each one answers:
# - "use only what is listed": code refuses any other name (check_plan), the rule saves the retry;
# - "records first": R71/R73 lost record questions to text reading (the router's 8 and 15 misses);
# - "read_check before counting or listing what documents say": R71 counted negated claims (F26-F28,
#   "no squeaking or wobbling" counted as wobbling); find_claims returns candidates, read_check verifies;
# - tone and time only with the question's words: G12 (R72) invented both; code drops them otherwise;
# - values in the property's type: R71's five empty held-out filters; code checks every value;
# - the ending follows the question's form: answers are scored as sets and numbers (R73's "answer form").
# The example's graph (hives and apiaries) is invented: no evaluated dataset uses it.
PROMPT = """You plan how to answer a question from the graph below. Write a query plan: a list of steps,
each one of the primitives listed, filled in with names from the graph. Code checks the plan, runs it and
computes the answer; you never write a database query.

The graph holds structured records (labels with properties, and relationships between records, which may
have properties of their own) and, when claim patterns are listed, the claims documents make: each claim
has a predicate, a subject entity and an object entity, comes from one chunk of text, and hangs on the
record its text is about.

{schema}

Primitives. `input` is the index (from 0) of an earlier step whose result a step works on.
- find_entity(name, label?): the records and entities a name names; two records with one name both come
  back. `label` narrows to a record label or an entity type.
- filter_records(label, property?, operator?, value?, input?): the records of a label whose property meets
  the condition, among the input's records when given; without a property, every record of the label.
- related(input, relationship, label?, property?, operator?, value?): the records at the other end of a
  relationship from the input's records; `property` compares a property of the relationship itself.
- find_claims(input?, predicate?, subject_like?, object_like?, tone?, tone_words?, time_words?,
  include_parts?): candidate claims about the input's records or entities. subject_like and object_like
  match entity names by spelling and by meaning, so the candidates can include near misses.
  include_parts also takes claims about the records that point at the input's records.
- read_check(input, statement): reads each candidate's own text and keeps the candidates whose text states
  the statement; at most 30 candidates, so narrow the input first.
- retrieve_chunks(input?): text: the input's own chunks, or without an input the chunks nearest the
  question.
Endings (the last step, and only the last):
- list(input, what?, property?): the names of the items; for claims, what = "about" (the records they are
  about), "subject" or "object"; with a property, the records' values of it.
- count(input, unit?): unit "items" (default), "documents" (distinct documents of the items' text) or
  "about" (distinct records the claims are about).
- sum(input, property): the total of a numeric property.
- rank(input, ...): with property and order "highest"/"lowest", the records with the best value; with
  relationship (and label) and order "most"/"fewest", the records at the other end linked to the most or
  fewest input records; for claims, the records with the most or fewest claims.
- answer_from_chunks(input?): a reader answers from the input's text, or from the chunks nearest the
  question.

Rules:
- Use only the labels, properties, relationships, predicates and entity types listed above.
- When a record's property or relationship holds the answer, answer from the records.
- To count or list what documents say, pass the candidates through read_check first: a claim can be
  negated or only possible.
- Set tone or time_words only when the question asks for them, with the question's own words in
  tone_words and time_words.
- Give each value in its property's type: a number as a number, a date as 'YYYY-MM-DD', a year with
  operator "year".
- End with list when the question asks which things, count or sum when it asks how many or how much, rank
  when it asks for the most, fewest, highest or lowest, and answer_from_chunks for anything else.

Example, for an invented graph with records (:Hive {{hive_id, built (INTEGER)}}), (:Apiary {{name}}),
(:Hive)-[:KEPT_AT]->(:Apiary), and claims "Hive HAS_STATE State":
Question: "How many hives kept at the Linden Apiary were reported as swarming?"
Steps: 0 find_entity(name "Linden Apiary", label "Apiary"); 1 related(input 0, relationship "KEPT_AT",
label "Hive"); 2 find_claims(input 1, object_like "swarming"); 3 read_check(input 2, statement "The hive
swarmed."); 4 count(input 3, unit "about").

<question>{question}</question>"""

# Appended for the one retry: the refused plan and the reasons, so the model can repair it.
RETRY = """

Your previous plan was refused:
<plan>{plan}</plan>
Reasons: {reasons}
Write a corrected plan."""


class Planner:
    """Writes query plans with one model call each (two with the retry)."""

    def __init__(self, llm: LLMClient, model: str, schema_text: str, temperature: float = 0.0):
        self._llm = llm
        self._model = model
        self._schema_text = schema_text
        self._temperature = temperature

    def propose(
        self, question: str, refused: QueryPlan | None = None, reasons: list[str] | None = None
    ) -> QueryPlan:
        """The model's plan; with `refused` and `reasons`, the corrected plan of the retry.

        Raises `LLMResponseError` when the model keeps failing (llm/retry.py)."""
        prompt = PROMPT.format(schema=self._schema_text, question=question)
        if refused is not None:
            prompt += RETRY.format(
                plan=refused.model_dump_json(exclude_defaults=True), reasons="; ".join(reasons or [])
            )
        return self._llm.generate(prompt, QueryPlan, model=self._model, temperature=self._temperature)
