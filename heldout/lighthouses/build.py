"""Build the lighthouses dataset: fetch the selected records once, then turn them into pipeline input.

Role: not part of the pipeline. It makes `heldout/lighthouses/data/`, a dataset of the R152-R157 benchmark,
which `kg --preset lighthouses ...` reads like any other data directory.
Design: two separate commands, as in `heldout/nhtsa/build.py`. `fetch` is the only part that uses the
network: it asks Wikidata for every lighthouse operated by Trinity House and English Wikipedia for the
selected articles, applies the selection rule and saves only the kept records under `raw/`. `build` is
pure: from `raw/` alone it writes `data/`, so a test can rebuild the committed dataset offline and prove
that nothing in it was edited by hand.
Not here: any cleaning of the article prose. It stays verbatim; only the section markers of the plain-text
extract become Markdown headings, so the benchmark measures the system on text it did not shape.
"""

import argparse
import csv
import json
import logging
import re
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
# The Wikimedia User-Agent policy asks for a descriptive agent with contact information; the project's
# public repository is the contact (requests without one may be refused with 403).
USER_AGENT = (
    "kgbuilder-thesis/1.0 (https://github.com/Habib1708-dev/Automated-Entity-Centric-Knowledge-Graph-"
    "Construction-from-Raw-Data-with-Natural-Language-Querying; dataset build script) Python-urllib"
)
# The selection, fixed before any article text was read. Tables: every item that is an instance of
# lighthouse (Q39715) operated by Trinity House (Q1570725), all of them, as NHTSA's "every recall".
# One simple query for the item list: queries that also aggregate the claims time out (504).
INSTANCE_OF, LIGHTHOUSE = "P31", "Q39715"
OPERATOR, TRINITY_HOUSE = "P137", "Q1570725"
# `wdt:` matches the best-rank values only, the same values `best_statements` keeps
ITEM_QUERY = (
    "SELECT DISTINCT ?item WHERE { "
    f"?item wdt:{INSTANCE_OF} wd:{LIGHTHOUSE}; wdt:{OPERATOR} wd:{TRINITY_HOUSE}. }}"
)
# lighthouses.csv: column -> Wikidata property. The properties are the ones most items have (scouted on 50
# items: inception 50, height 49, county 49, light characteristic 41 ... structural engineer 7).
COLUMNS = {
    "inception": "P571",
    "height_m": "P2048",
    "focal_height_m": "P2923",
    "range_nmi": "P2929",
    "light_characteristic": "P1030",
    "historic_county": "P7959",
    "located_in": "P131",
    "heritage_designation": "P1435",
    "material": "P186",
    "service_entry": "P729",
    "service_retirement": "P730",
    "architect": "P84",
    "structural_engineer": "P631",
}
EVENT, EVENT_DATE = "P793", "P585"  # significant event, and its point-in-time qualifier
ENGINEER_PROPERTIES = ("architect", "structural_engineer")  # the columns whose values are engineers.json
PERSON_COLUMNS = {"birth": "P569", "death": "P570", "father": "P22", "sibling": "P3373"}
# Documents: an item's own article. The title must contain "Lighthouse" (drops place articles such as
# "Coquet Island") and its page must belong to this item alone (drops shared pages such as "Harwich High
# and Low Lighthouses", which two items link to through redirects). The byte window plays the role of
# NHTSA's 40-110 word rule: under 6,000 bytes are stubs; over 10,000 the long famous articles (Eddystone
# 43.6 kB, Bishop Rock 29.7 kB), which are the ones a model is most likely to remember. Lowest Q-id first,
# ten of the 13 articles in the window: at about 72 words per kB of wikitext, about 5,000-6,500 words
# (2.5-3x NHTSA's 1,974), enough for 50 questions without rewording and cheap to build.
TITLE_WORD = "Lighthouse"
MIN_BYTES, MAX_BYTES = 6_000, 10_000
DOCUMENTS = 10
SEPARATOR = "; "  # joins a multi-valued field in the CSV; no label of the selection contains it
_BATCH = 50  # the most ids or titles the two APIs accept in one request


def _write(path: Path, text: str) -> None:
    # "\n" on every platform: the committed files must not depend on the machine that built them
    path.write_text(text, encoding="utf-8", newline="\n")


def _json(value: object) -> str:
    # accents and symbols stay readable in the committed files instead of \u escapes
    return json.dumps(value, indent=1, ensure_ascii=False) + "\n"


def _read(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def qid_number(qid: str) -> int:
    """The numeric part of a Wikidata id ("Q1570725" -> 1570725): ids sort by it, not as text."""
    return int(qid[1:])


def best_statements(statements: list[dict]) -> list[dict]:
    """The statements Wikidata itself treats as true: the preferred ones, else every normal-rank one.

    Deprecated statements (known to be wrong, or superseded) are never kept.
    """
    preferred = [st for st in statements if st["rank"] == "preferred"]
    return preferred or [st for st in statements if st["rank"] == "normal"]


def slug(title: str) -> str:
    """A file name from an article title ("St Agnes Lighthouse" -> "st_agnes_lighthouse")."""
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")


# ---- fetch (network) -------------------------------------------------------------------------------


def _get(url: str, params: dict[str, str]) -> dict:
    """One API response as JSON. Raises `urllib.error.URLError` when the service cannot be reached."""
    query = urllib.parse.urlencode({**params, "format": "json"})
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def _entities(ids: list[str], props: str) -> dict[str, dict]:
    """Wikidata entities by id, in batches; labels in English with `mul` and en-gb as fallbacks."""
    found: dict[str, dict] = {}
    for start in range(0, len(ids), _BATCH):
        params = {
            "action": "wbgetentities",
            "ids": "|".join(ids[start : start + _BATCH]),
            "props": props,
            "languages": "en|mul|en-gb",
            "sitefilter": "enwiki",
        }
        found.update(_get(WIKIDATA_API, params)["entities"])
    return found


def label_of(entity: dict) -> str | None:
    """The entity's English label; the language-neutral `mul` label, then en-gb, when it has none.

    The label service left some items unnamed in English (the historic county Q23148, Cornwall), which
    is why the fallback exists.
    """
    labels = entity.get("labels", {})
    return next((labels[lang]["value"] for lang in ("en", "mul", "en-gb") if lang in labels), None)


def _snak(snak: dict) -> dict:
    # the hash and property name repeat what the statement already says
    return {k: snak[k] for k in ("snaktype", "datatype", "datavalue") if k in snak}


def _statement(statement: dict, prop: str) -> dict:
    """A statement reduced to its rank, its value and, for an event, its date.

    References, statement ids and every other qualifier are dropped: the build reads none of them, and
    references alone are most of the bytes. One consequence: the values of a multi-valued cell (a range
    per light colour, via P462) lose what told them apart; the README's risks say so.
    """
    kept = {"rank": statement["rank"], "mainsnak": _snak(statement["mainsnak"])}
    dates = statement.get("qualifiers", {}).get(EVENT_DATE, [])
    if prop == EVENT and dates:
        kept["qualifiers"] = {EVENT_DATE: [_snak(s) for s in dates]}
    return kept


def _record(entity: dict, properties: set[str]) -> dict:
    """An entity reduced to what the build reads: id, label, enwiki title, and the chosen claims."""
    claims = {
        prop: [_statement(st, prop) for st in statements]
        for prop, statements in entity.get("claims", {}).items()
        if prop in properties
    }
    sitelink = entity.get("sitelinks", {}).get("enwiki", {}).get("title")
    return {"qid": entity["id"], "label": label_of(entity), "enwiki": sitelink, "claims": claims}


def _item_ids(records: list[dict]) -> set[str]:
    """Every item a record's values point at (a county, an event type, a person), to fetch its label."""
    values = [
        st["mainsnak"]["datavalue"]["value"]
        for record in records
        for statements in record["claims"].values()
        for st in statements
        if "datavalue" in st["mainsnak"]
    ]
    return {v["id"] for v in values if isinstance(v, dict) and "id" in v}


def _page_info(titles: list[str]) -> dict[str, dict]:
    """Per sitelink title: the page it resolves to (through redirects), its byte length and last revision."""
    info: dict[str, dict] = {}
    for start in range(0, len(titles), _BATCH):
        batch = titles[start : start + _BATCH]
        params = {"action": "query", "prop": "info", "titles": "|".join(batch), "redirects": "1"}
        result = _get(WIKIPEDIA_API, params)["query"]
        # a title may be normalised ("_" -> " "), then redirected; follow both maps to the final page
        step = {m["from"]: m["to"] for m in result.get("normalized", []) + result.get("redirects", [])}
        pages = {p["title"]: p for p in result["pages"].values()}
        for title in batch:
            final = title
            while final in step:
                final = step[final]
            page = pages[final]
            info[title] = {"title": final, "length": page.get("length"), "revid": page.get("lastrevid")}
    return info


def select_documents(candidates: list[dict]) -> list[dict]:
    """The candidates the document rule keeps: own "Lighthouse" page, 6,000-10,000 bytes, lowest Q-id first.

    `candidates` are the selected items with an enwiki article: `qid`, `title` (after redirects), `length`.
    """
    owners = Counter(c["title"] for c in candidates)
    eligible = [
        c
        for c in sorted(candidates, key=lambda c: qid_number(c["qid"]))
        if TITLE_WORD in c["title"] and owners[c["title"]] == 1 and MIN_BYTES <= c["length"] <= MAX_BYTES
    ]
    return eligible[:DOCUMENTS]


def _article(title: str) -> dict:
    """An article's plain-text extract with its revision id and timestamp (TextExtracts, wiki headings)."""
    params = {
        "action": "query",
        "prop": "extracts|revisions",
        "explaintext": "1",
        "exsectionformat": "wiki",
        "rvprop": "ids|timestamp",
        "titles": title,
    }
    page = next(iter(_get(WIKIPEDIA_API, params)["query"]["pages"].values()))
    revision = page["revisions"][0]
    return {
        "title": page["title"],
        "pageid": page["pageid"],
        "revid": revision["revid"],
        "timestamp": revision["timestamp"],
        "extract": page["extract"],
    }


def _fetch_tables(today: str) -> dict:
    """The lighthouses, their engineers and every label they need, as the raw files' contents."""
    rows = _get(SPARQL, {"query": ITEM_QUERY})["results"]["bindings"]
    ids = sorted({r["item"]["value"].rsplit("/", 1)[-1] for r in rows}, key=qid_number)
    # the two selecting properties are kept too, so a test can check the selection offline
    wanted = {*COLUMNS.values(), EVENT, INSTANCE_OF, OPERATOR}
    entities = _entities(ids, "labels|claims|sitelinks")
    lighthouses = [_record(entities[qid], wanted) for qid in ids]
    people_ids = sorted(
        {
            st["mainsnak"]["datavalue"]["value"]["id"]
            for record in lighthouses
            for column in ENGINEER_PROPERTIES
            for st in best_statements(record["claims"].get(COLUMNS[column], []))
            if "datavalue" in st["mainsnak"]
        },
        key=qid_number,
    )
    people = _entities(people_ids, "labels|claims")
    engineers = [_record(people[qid], set(PERSON_COLUMNS.values())) for qid in people_ids]
    label_ids = sorted(_item_ids(lighthouses + engineers), key=qid_number)
    labels = {qid: label_of(e) for qid, e in _entities(label_ids, "labels").items()}
    return {
        "fetched": today,
        "query": ITEM_QUERY,
        "lighthouses": lighthouses,
        "engineers": engineers,
        "labels": labels,
    }


def fetch(raw_dir: Path) -> None:
    """Download the selected Wikidata records and Wikipedia articles into `raw_dir`."""
    today = date.today().isoformat()
    (raw_dir / "articles").mkdir(parents=True, exist_ok=True)
    tables = _fetch_tables(today)
    _write(raw_dir / "wikidata.json", _json(tables))
    linked = [r for r in tables["lighthouses"] if r["enwiki"]]
    info = _page_info([r["enwiki"] for r in linked])
    candidates = [{"qid": r["qid"], "sitelink": r["enwiki"], **info[r["enwiki"]]} for r in linked]
    kept = select_documents(candidates)
    _write(raw_dir / "documents.json", _json({"fetched": today, "candidates": candidates}))
    for candidate in kept:
        article = {"fetched": today, "qid": candidate["qid"], **_article(candidate["title"])}
        _write(raw_dir / "articles" / f"{slug(article['title'])}.json", _json(article))
    log.info(
        "%d lighthouses, %d engineers, %d of %d linked articles kept",
        len(tables["lighthouses"]),
        len(tables["engineers"]),
        len(kept),
        len(candidates),
    )


# ---- build (offline, deterministic) ----------------------------------------------------------------

_ENTITY = "http://www.wikidata.org/entity/"
# Quantity units seen in the selection. Heights are in metres except a few in feet, which are converted
# so that a column holds one unit; any other unit stops the build instead of mixing units silently.
_METRE, _FOOT, _NAUTICAL_MILE = f"{_ENTITY}Q11573", f"{_ENTITY}Q3710", f"{_ENTITY}Q93318"
_METRES_PER_FOOT = 0.3048  # exact by definition; the result is rounded to the centimetre
# Wikidata time precision -> characters of the ISO date kept: 9 year, 10 month, 11 day. The selection
# holds no coarser date (a decade, a century), so a KeyError here means the raw data changed.
_DATE_CHARS = {9: 4, 10: 7, 11: 10}
# a section marker of the plain-text extract: "== History ==" (level 2) to "====== x ======" (level 6)
_HEADING = re.compile(r"^(={2,6}) *(.+?) *\1$")


def _quantity(value: dict) -> str:
    amount = value["amount"].lstrip("+")
    if value["unit"] in (_METRE, _NAUTICAL_MILE):
        return amount
    if value["unit"] == _FOOT:
        return f"{float(amount) * _METRES_PER_FOOT:.2f}"
    raise ValueError(f"unexpected unit {value['unit']}")


def _render(snak: dict, labels: dict[str, str | None]) -> str | None:
    """One value as text: an item by its label (its id when it has none), a date to its precision."""
    if snak["snaktype"] != "value":  # "no value" / "unknown value": nothing to state
        return None
    value, kind = snak["datavalue"]["value"], snak["datavalue"]["type"]
    if kind == "wikibase-entityid":
        return labels.get(value["id"]) or value["id"]
    if kind == "time":
        return value["time"].lstrip("+")[: _DATE_CHARS[value["precision"]]]
    if kind == "quantity":
        return _quantity(value)
    if kind == "monolingualtext":
        return value["text"]
    return value


def _values(record: dict, prop: str, labels: dict[str, str | None]) -> list[str]:
    """The distinct rendered values of one property, in statement order.

    Two statements can render to the same text once their distinguishing qualifier (a sector's colour,
    a listing's date) is dropped in `raw/`, as in a range "17; 14; 14"; a repeat would say nothing and
    would inflate a count of values, so each value is kept once.
    """
    rendered = (_render(st["mainsnak"], labels) for st in best_statements(record["claims"].get(prop, [])))
    return list(dict.fromkeys(v for v in rendered if v is not None))


def _field(values: list[str]) -> str | list[str] | None:
    """A JSON field: one value as itself, several as a list, none as null."""
    if len(values) > 1:
        return values
    return values[0] if values else None


def _lighthouse_rows(tables: dict) -> list[dict]:
    """One row per lighthouse, multi-valued fields joined by `SEPARATOR`.

    `enwiki_article` is the item's English Wikipedia sitelink, empty without one. The Wikidata name and
    the article title can differ ("Portland Bill High Light" is the item of "Old Higher Lighthouse"); the
    title is the column that names each document, but linking reads it only if the plan chooses it as
    the name column (the default guess is `name`).
    """
    labels = tables["labels"]
    return [
        {
            "qid": r["qid"],
            "name": r["label"],
            "enwiki_article": r["enwiki"] or "",
            **{col: SEPARATOR.join(_values(r, prop, labels)) for col, prop in COLUMNS.items()},
        }
        for r in tables["lighthouses"]
    ]


def _events(tables: dict) -> list[dict]:
    """One row per significant-event statement, the lighthouse as a nested object (staged to dotted columns).

    Every statement but the deprecated ones: events are things that happened, not rival values of one
    fact, so the preferred-rank rule of the table does not apply. The date is the point-in-time qualifier,
    null when the statement has none.
    """
    rows = []
    for r in tables["lighthouses"]:
        for st in r["claims"].get(EVENT, []):
            event = _render(st["mainsnak"], tables["labels"])
            if st["rank"] == "deprecated" or event is None:
                continue
            dates = [_render(s, tables["labels"]) for s in st.get("qualifiers", {}).get(EVENT_DATE, [])]
            rows.append(
                {
                    "lighthouse": {"qid": r["qid"], "name": r["label"]},
                    "event": event,
                    "date": _field([d for d in dates if d is not None]),
                }
            )
    return rows


def _engineers(tables: dict) -> list[dict]:
    labels = tables["labels"]
    return [
        {
            "qid": e["qid"],
            "name": e["label"],
            **{col: _field(_values(e, prop, labels)) for col, prop in PERSON_COLUMNS.items()},
        }
        for e in tables["engineers"]
    ]


def _has_prose(lines: list[str], level: int) -> bool:
    """Whether text follows before the next heading of the same or a higher level."""
    for line in lines:
        heading = _HEADING.match(line)
        if heading and len(heading.group(1)) <= level:
            return False
        if not heading and line.strip():
            return True
    return False


def markdown(title: str, extract: str) -> str:
    """An article as Markdown: its title as `#`, each section marker as `##`-`######`, prose verbatim.

    Empty sections are dropped (the extract keeps the heading of "See also" or "References" while
    dropping their lists), and so are the runs of blank lines they leave: whitespace only, no prose.
    """
    lines = extract.strip().splitlines()
    out = [f"# {title}", ""]
    for i, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading is None:
            out.append(line)
        elif _has_prose(lines[i + 1 :], len(heading.group(1))):
            out.append(f"{'#' * len(heading.group(1))} {heading.group(2)}")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip("\n") + "\n"


def _articles(raw_dir: Path) -> list[dict]:
    # sorted by file name, so the output does not depend on directory order
    return [_read(p) for p in sorted((raw_dir / "articles").glob("*.json"))]


def build(raw_dir: Path, data_dir: Path) -> None:
    """Write the dataset: `lighthouses.csv`, `events.ndjson`, `engineers.json`, `articles/*.md`."""
    tables = _read(raw_dir / "wikidata.json")
    (data_dir / "articles").mkdir(parents=True, exist_ok=True)
    with (data_dir / "lighthouses.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["qid", "name", "enwiki_article", *COLUMNS], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(_lighthouse_rows(tables))
    lines = [json.dumps(event, ensure_ascii=False) for event in _events(tables)]
    _write(data_dir / "events.ndjson", "\n".join(lines) + "\n")
    _write(data_dir / "engineers.json", _json(_engineers(tables)))
    for article in _articles(raw_dir):
        document = markdown(article["title"], article["extract"])
        _write(data_dir / "articles" / f"{slug(article['title'])}.md", document)


def attribution(raw_dir: Path) -> str:
    """ATTRIBUTION.md: per article its title, URL, revision, history link and licence (CC BY-SA 4.0)."""
    articles = _articles(raw_dir)
    entries = []
    for article in articles:
        page = urllib.parse.quote(article["title"].replace(" ", "_"), safe="'(),")
        entries.append(
            f"- **{article['title']}** (`data/articles/{slug(article['title'])}.md`): "
            f"https://en.wikipedia.org/wiki/{page}, revision "
            f"[{article['revid']}](https://en.wikipedia.org/w/index.php?oldid={article['revid']}) of "
            f"{article['timestamp']}; authors in the [page history]"
            f"(https://en.wikipedia.org/w/index.php?title={page}&action=history)."
        )
    return (
        "# Attribution\n\n"
        "The documents in `data/articles/` are the plain text of English Wikipedia articles, written by "
        "the Wikipedia contributors listed in each page history, fetched on "
        f"{articles[0]['fetched']} and licensed under the Creative Commons Attribution-ShareAlike 4.0 "
        "International licence (CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/).\n\n"
        "Changes made: the plain-text extract leaves out the infobox, tables, images and references and "
        "flattens the rest to plain text; section markers became Markdown headings, empty sections were "
        "dropped, and the article title was added as the first heading. The prose is unchanged.\n\n"
        "This file is written by `build.py` from `raw/`; do not edit it by hand.\n\n"
        + "\n".join(entries)
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["fetch", "build"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "fetch":
        fetch(HERE / "raw")
    else:
        build(HERE / "raw", HERE / "data")
        _write(HERE / "ATTRIBUTION.md", attribution(HERE / "raw"))


if __name__ == "__main__":
    main()
