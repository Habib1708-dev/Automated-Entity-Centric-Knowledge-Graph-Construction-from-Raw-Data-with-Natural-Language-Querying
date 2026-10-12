"""Build the NTSB aviation accidents dataset: fetch the selected records once, then build pipeline input.

Role: not part of the pipeline. It makes `heldout/ntsb/data/`, a dataset of the R152-R157 benchmark, which
`kg --preset ntsb ...` reads like any other data directory.
Design: two separate commands, as in `heldout/nhtsa/build.py`. `fetch` is the only part that uses the
network: it downloads the NTSB's MS Access export `avall.zip` once into a cache directory outside the
repository, reads it with the pure-Python `access-parser` (not a project dependency: run it as
`uv run --with access-parser==0.0.6 python heldout/ntsb/build.py fetch`; the text fix in `_Tables` is written
for that version's internals), applies the selection rule and saves only
the kept events' rows of every used table, and the code meanings they need, under `raw/`. `build` is pure
(standard library only): from `raw/` alone it writes `data/`, so a test can rebuild the committed dataset
offline and prove that nothing in it was edited by hand.
Not here: any cleaning of the narratives. Investigators' prose stays verbatim; only its line endings are
made "\n" and its outer whitespace dropped, so the benchmark measures the system on text it did not shape.
"""

import argparse
import csv
import json
import logging
import re
import tempfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date
from importlib import metadata
from pathlib import Path

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SOURCE = "https://data.ntsb.gov/avdata/FileDirectory/DownloadFile?fileID=C%3A%5Cavdata%5Cavall.zip"
ZIP_NAME, MDB_NAME = "avall.zip", "avall.mdb"
# The 97 MB download and the 561 MB database stay outside the repository; only the selected rows are kept.
DEFAULT_CACHE = Path(tempfile.gettempdir()) / "kgbuilder-ntsb"

# ---- the selection rule, fixed before any narrative was read ---------------------------------------
# Accidents (not incidents) of 2021-2023: old enough that the final report and its probable cause exist.
EVENT_TYPE = "ACC"
FIRST_DAY, LAST_DAY = "2021-01-01", "2023-12-31"
# Six models of six makes, as (make, model) the way the documents name them. Airplanes and a helicopter,
# personal, training and agricultural flying, piston and turbine engines. Checked on counts only before the
# rule was frozen: eligible events per model 22 / 19 / 15 / 11 / 29 / 7, every one at least 5.
MODELS = [
    ("Cessna", "172S"),
    ("Piper", "PA-28-181"),
    ("Cirrus", "SR22"),
    ("Beech", "A36"),
    ("Robinson", "R44 II"),
    ("Air Tractor", "AT-502B"),
]
EVENTS_PER_MODEL = 5
# narr_accf words: under 100 is a one-paragraph note with little to ask about, over 250 a long major
# investigation that would dominate a small corpus; 30 events then give about 4,500-6,000 words with the
# probable causes, 2-3x NHTSA's 1,974.
MIN_WORDS, MAX_WORDS = 100, 250
# "-" and spaces vary in the source ("SR-22", "PA28181"), and the make is spelled per record ("CIRRUS",
# "CIRRUS DESIGN CORP", "Cirrus Aircraft"): a make matches when the reported one starts with it.
# Only acft_model is compared, never acft_series, and a successor make is no prefix of the selected one, so
# these records of the six models are not candidates (the rule was frozen and run before this was found):
# a model split over model and series ("172" / "S", "R44" / "II", "PA28" / "181", "AT502" / "B") and the
# makes TEXTRON AVIATION INC (Cessna 172S) and RAYTHEON AIRCRAFT COMPANY (Beech A36). In the 2026-10-01
# export that is 56 single-aircraft accidents of the window, 16 of them in the word window with a cause.
_SPELLING = re.compile(r"[\s-]")


def _names(text: str) -> list[str]:
    """Column names written as one space-separated string, so the lists below stay short to read."""
    return text.split()


# ---- what raw/ keeps of each table -----------------------------------------------------------------
# Only the columns the build or the rule reads. Left out on purpose: owner names and addresses, the
# operator's address, the crew's home city and state, the NTSB staff user ids (lchg_userid).
TABLES: dict[str, list[str]] = {
    "events": _names(
        "ev_id ntsb_no ev_type ev_date ev_time ev_city ev_state ev_country apt_name ev_nr_apt_id "
        "apt_dist light_cond wx_cond_basic wind_vel_kts gust_kts ev_highest_injury "
        "inj_tot_f inj_tot_s inj_tot_m inj_tot_n dec_latitude dec_longitude"
    ),
    "aircraft": _names(
        "ev_id Aircraft_Key ntsb_no regis_no acft_make acft_model acft_series acft_year "
        "acft_category far_part damage num_eng total_seats oper_individual_name oper_name oper_dba "
        "type_fly dprt_apt_id dest_apt_id"
    ),
    "engines": _names(
        "ev_id Aircraft_Key eng_no eng_type eng_mfgr eng_model power_units hp_or_lbs carb_fuel_injection"
    ),
    # Cause_Factor and cm_inPc are kept so a test can show why data/ drops them: empty on every kept row
    "Findings": _names("ev_id Aircraft_Key finding_no finding_code finding_description Cause_Factor cm_inPc"),
    "Events_Sequence": _names(
        "ev_id Aircraft_Key Occurrence_No Occurrence_Code Occurrence_Description Defining_ev"
    ),
    "Flight_Crew": _names(
        "ev_id Aircraft_Key crew_no crew_category crew_age crew_sex med_certf med_crtf_vldty "
        "crew_inj_level seat_occ_pic"
    ),
    "flight_time": _names("ev_id Aircraft_Key crew_no flight_type flight_craft flight_hours"),
    "injury": _names("ev_id Aircraft_Key inj_person_category injury_level inj_person_count"),
    "narratives": _names("ev_id Aircraft_Key narr_accf narr_cause"),
}
# The coded columns data/ shows as words, per table; their meanings come from the database's own data
# dictionary (eADMSPUB_DataDictionary), else from its code table (ct_iaids) under the column's code-table
# name. A code neither defines stays as it is: no meaning is guessed.
DECODED: dict[str, list[str]] = {
    "events": ["light_cond", "wx_cond_basic", "ev_highest_injury"],
    "aircraft": ["acft_category", "far_part", "damage", "type_fly"],
    "engines": ["eng_type", "hp_or_lbs", "carb_fuel_injection"],
    "Flight_Crew": [
        "crew_category",
        "crew_sex",
        "med_certf",
        "med_crtf_vldty",
        "crew_inj_level",
        "seat_occ_pic",
    ],
    "injury": ["inj_person_category", "injury_level"],
}
# crew.csv columns pivoted from flight_time: (flight_type, flight_craft) -> column. All aircraft, except the
# hours in the accident aircraft's make and model.
HOURS = {
    ("TOTL", "ALL"): "total_hours",
    ("PIC", "ALL"): "pilot_in_command_hours",
    ("TOTL", "MAKE"): "make_model_hours",
    ("L90D", "ALL"): "last_90_days_hours",
    ("L30D", "ALL"): "last_30_days_hours",
    ("L24H", "ALL"): "last_24_hours_hours",
}


def _write(path: Path, text: str) -> None:
    # "\n" on every platform: the committed files must not depend on the machine that built them
    path.write_text(text, encoding="utf-8", newline="\n")


def _json(value: object) -> str:
    # accents and symbols stay readable in the committed files instead of \u escapes
    return json.dumps(value, indent=1, ensure_ascii=False) + "\n"


def _read(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def _spelling(text: str | None) -> str:
    return _SPELLING.sub("", (text or "").upper())


def model_of(make: str | None, model: str | None) -> tuple[str, str] | None:
    """The selected (make, model) of `MODELS` a reported make and model belong to, or None.

    The model must equal the selected one and the make start with its make, both upper-cased with spaces
    and hyphens removed: "CIRRUS DESIGN CORP" / "SR-22" is the Cirrus SR22, "CIRRUS" / "SR22T" is not.
    The series is not read, so "CESSNA" / "172" (series "S") is not the Cessna 172S (see `_SPELLING`).
    """
    for fixed_make, fixed_model in MODELS:
        if _spelling(make).startswith(_spelling(fixed_make)) and _spelling(model) == _spelling(fixed_model):
            return fixed_make, fixed_model
    return None


def model_name(make_model: tuple[str, str]) -> str:
    """The name a model goes by in models.csv and the documents ("Cirrus SR22")."""
    return " ".join(make_model)


def words(text: str | None) -> int:
    """The word count the rule reads: whitespace-separated tokens, as NHTSA's 40-110 word rule counts."""
    return len((text or "").split())


def select_events(candidates: list[dict]) -> list[dict]:
    """The candidates the rule keeps: per model, in `MODELS` order, the 5 lowest NTSB numbers whose factual
    narrative has 100-250 words and whose probable cause is not empty.

    `candidates` are the accidents of the date window with one aircraft of a selected model: `model`,
    `ntsb_no`, `narr_accf_words`, `has_probable_cause`. Raises `ValueError` when a model has fewer than five
    eligible events (the source changed; the rule must be revisited, not quietly shrunk).
    """
    kept = []
    for make_model in MODELS:
        name = model_name(make_model)
        eligible = [
            c
            for c in sorted(candidates, key=lambda c: c["ntsb_no"])
            if c["model"] == name
            and MIN_WORDS <= c["narr_accf_words"] <= MAX_WORDS
            and c["has_probable_cause"]
        ]
        if len(eligible) < EVENTS_PER_MODEL:
            raise ValueError(f"{name}: {len(eligible)} eligible events, the rule needs {EVENTS_PER_MODEL}")
        kept += eligible[:EVENTS_PER_MODEL]
    return kept


def slug(text: str) -> str:
    """A file name part ("Air Tractor AT-502B" -> "air_tractor_at_502b")."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


# ---- fetch (network, then the cached database) -----------------------------------------------------


def _database(cache: Path) -> Path:
    """The path of avall.mdb in `cache`, downloading and unzipping it only when it is not there yet."""
    cache.mkdir(parents=True, exist_ok=True)
    archive, mdb = cache / ZIP_NAME, cache / MDB_NAME
    if not archive.exists():
        log.info("downloading %s (about 97 MB)", SOURCE)
        request = urllib.request.Request(SOURCE, headers={"User-Agent": "kgbuilder-thesis"})
        with urllib.request.urlopen(request, timeout=1800) as response:
            archive.write_bytes(response.read())
    if not mdb.exists():
        with zipfile.ZipFile(archive) as z:
            z.extract(MDB_NAME, cache)
    return mdb


def _source_date(cache: Path) -> str:
    """The date the NTSB wrote avall.mdb (its time stamp inside the zip): which monthly refresh was read."""
    with zipfile.ZipFile(cache / ZIP_NAME) as z:
        stamp = z.getinfo(MDB_NAME).date_time
    return date(*stamp[:3]).isoformat()


COMPRESSED_MARK = b"\xff\xfe"  # starts a Jet 4 text value stored in "compressed unicode"
# The one access-parser release `_Tables` patches: the patch relies on its internals (where parse_type is
# looked up, its signature), so another release could fail, or skip or double the fix without an error.
ACCESS_PARSER_VERSION = "0.0.6"


def check_reader_version(installed: str) -> None:
    """Raise `ImportError` unless `installed` is `ACCESS_PARSER_VERSION`, the release the text fix is for."""
    if installed != ACCESS_PARSER_VERSION:
        raise ImportError(
            f"access-parser {installed} is installed, the text fix is written for {ACCESS_PARSER_VERSION}: "
            f"run with `uv run --with access-parser=={ACCESS_PARSER_VERSION}`"
        )


def decompress_text(data: bytes) -> str:
    """A Jet 4 "compressed unicode" text value (it starts with `COMPRESSED_MARK`) as a string.

    After the mark, each byte is one character (Latin-1) until a 0x00 byte switches to two bytes per
    character (UTF-16LE); the next 0x00 switches back. The rule is the one mdbtools applies. access-parser
    0.0.6 ignores the switches and only drops the 0x00 bytes, so "O’Hare" (b"O\\x00\\x19\\x20\\x00Hare")
    came out as "O\\x19 Hare": every curly quote and dash broken, and a word count one too high.
    """
    decoded, one_byte, i = bytearray(), True, len(COMPRESSED_MARK)
    while i < len(data):
        if data[i] == 0:
            one_byte = not one_byte
            i += 1
        elif one_byte:
            decoded += bytes([data[i], 0])
            i += 1
        elif i + 1 < len(data):
            decoded += data[i : i + 2]
            i += 2
        else:  # a lone trailing byte in two-byte mode is not a character
            break
    return decoded.decode("utf-16-le")


class _Tables:
    """Reads whole tables of the database as row dicts (access-parser returns them column-wise)."""

    def __init__(self, mdb: Path) -> None:
        check_reader_version(metadata.version("access-parser"))
        # a fetch-only tool, not a project dependency
        import access_parser.access_parser as reader
        from access_parser.utils import TYPE_TEXT

        parse_type = reader.parse_type

        def parse_with_text_fix(
            data_type: int,
            buffer: bytes,
            length: int | None = None,
            version: int = 3,
            props: dict | None = None,
        ) -> object:  # whatever access-parser's own parse_type returns for that type
            # access-parser looks parse_type up in its module for every value, text and memo alike, so
            # replacing it there is the one place to decode compressed text correctly (see decompress_text)
            if data_type == TYPE_TEXT and version > 3 and buffer.startswith(COMPRESSED_MARK):
                return decompress_text(bytes(buffer))
            return parse_type(data_type, buffer, length, version, props)

        reader.parse_type = parse_with_text_fix
        self._db = reader.AccessParser(str(mdb))

    def rows(self, table: str) -> list[dict]:
        """Every row of `table`, as {column: value}."""
        columns = self._db.parse_table(table)
        return [dict(zip(columns, values, strict=True)) for values in zip(*columns.values(), strict=True)]


def _candidates(events: list[dict], aircraft: list[dict], narratives: list[dict]) -> list[dict]:
    """Every accident of the window with exactly one aircraft of a selected model, with the narrative counts
    the rule needs (word count, whether a probable cause exists): counts, not text."""
    in_window = {
        e["ev_id"]: e
        for e in events
        if e["ev_type"] == EVENT_TYPE and FIRST_DAY <= (e["ev_date"] or "")[:10] <= LAST_DAY
    }
    per_event = Counter(a["ev_id"] for a in aircraft)
    narrative = {(n["ev_id"], n["Aircraft_Key"]): n for n in narratives}
    candidates = []
    for a in aircraft:
        model = model_of(a["acft_make"], a["acft_model"])
        if a["ev_id"] not in in_window or per_event[a["ev_id"]] != 1 or model is None:
            continue
        n = narrative.get((a["ev_id"], a["Aircraft_Key"]), {})
        candidates.append(
            {
                "ev_id": a["ev_id"],
                "ntsb_no": in_window[a["ev_id"]]["ntsb_no"],
                "ev_date": in_window[a["ev_id"]]["ev_date"],
                "acft_make": a["acft_make"],
                "acft_model": a["acft_model"],
                "model": model_name(model),
                "narr_accf_words": words(n.get("narr_accf")),
                "has_probable_cause": bool((n.get("narr_cause") or "").strip()),
            }
        )
    return sorted(candidates, key=lambda c: c["ntsb_no"])


def _kept_rows(table: str, rows: list[dict], kept: set[str]) -> list[dict]:
    """The kept events' rows of one table, reduced to `TABLES[table]`, in a fixed order.

    An individual operator's name and the aircraft's registration are cleared here, before anything is
    saved, where the NTSB marks the operator as a private individual (oper_individual_name "Y"). The NTSB
    already leaves the name empty in the selected years; this makes it a rule. The registration goes too
    because the public FAA registry turns an N-number into its owner's name and address.
    """
    columns = TABLES[table]
    selected = [{c: r[c] for c in columns} for r in rows if r["ev_id"] in kept]
    for r in selected:
        if r.get("oper_individual_name") == "Y":
            r["oper_name"] = None
            r["regis_no"] = None
    return sorted(selected, key=lambda r: json.dumps([r[c] for c in columns], default=str))


def _codes(dictionary: list[dict], code_table: list[dict]) -> dict[str, dict[str, str]]:
    """Per decoded "table.column", every code's meaning: the data dictionary's, else the code table's.

    Codes are compared upper-cased and stripped: the data says "Unk" where the dictionary defines "UNK".
    """
    by_code_table: dict[str, dict[str, str]] = defaultdict(dict)
    for row in code_table:
        if row["ct_name"] and row["code_iaids"] and row["meaning"]:
            by_code_table[row["ct_name"]][row["code_iaids"].strip().upper()] = row["meaning"]
    codes: dict[str, dict[str, str]] = {}
    for table, columns in DECODED.items():
        for column in columns:
            entries = [
                d for d in dictionary if (d["Table"] or "").lower() == table.lower() and d["Column"] == column
            ]
            meanings: dict[str, str] = {}
            for name in sorted({d["ct_name"] for d in entries if d["ct_name"]}):
                meanings.update(by_code_table[name])
            meanings.update(
                {d["code_iaids"].strip().upper(): d["meaning"] for d in entries if d["code_iaids"]}
            )
            codes[f"{table}.{column}"] = dict(sorted(meanings.items()))
    return codes


def fetch(raw_dir: Path, cache: Path) -> None:
    """Select the events from the cached database and save their rows and code meanings into `raw_dir`."""
    mdb = _database(cache)
    stamp = {"fetched": date.today().isoformat(), "source": SOURCE, "source_date": _source_date(cache)}
    tables = _Tables(mdb)
    rows = {name: tables.rows(name) for name in ("events", "aircraft", "narratives")}
    candidates = _candidates(rows["events"], rows["aircraft"], rows["narratives"])
    kept = {c["ev_id"] for c in select_events(candidates)}
    for name in TABLES:
        if name not in rows:
            rows[name] = tables.rows(name)
    raw_dir.mkdir(parents=True, exist_ok=True)
    _write(raw_dir / "selection.json", _json({**stamp, "candidates": candidates}))
    kept_tables = {name: _kept_rows(name, rows[name], kept) for name in TABLES}
    _write(raw_dir / "tables.json", _json({**stamp, "tables": kept_tables}))
    codes = _codes(tables.rows("eADMSPUB_DataDictionary"), tables.rows("ct_iaids"))
    _write(raw_dir / "codes.json", _json({**stamp, "codes": codes}))
    log.info("%d of %d candidate events kept", len(kept), len(candidates))


# ---- build (offline, deterministic, standard library only) -----------------------------------------


def _strip(value: object) -> object:
    # an empty text value says nothing: written as null like the source's missing values
    return (value.strip() or None) if isinstance(value, str) else value


class _Raw:
    """The raw records with the lookups every output file needs: codes, NTSB numbers, models, event order."""

    def __init__(self, raw_dir: Path) -> None:
        tables: dict[str, list[dict]] = _read(raw_dir / "tables.json")["tables"]
        # outer spaces of a table value are dropped ("West Liberty " is the city "West Liberty"); the
        # narratives are left to `_text`, which keeps their inner layout
        self.tables = {
            name: rows if name == "narratives" else [{k: _strip(v) for k, v in r.items()} for r in rows]
            for name, rows in tables.items()
        }
        self.codes: dict[str, dict[str, str]] = _read(raw_dir / "codes.json")["codes"]
        self.ntsb_no = {e["ev_id"]: e["ntsb_no"] for e in self.tables["events"]}
        self.model: dict[str, tuple[str, str]] = {}
        for a in self.tables["aircraft"]:
            model = model_of(a["acft_make"], a["acft_model"])
            if model is None:
                raise ValueError(
                    f"{a['ntsb_no']}: {a['acft_make']} {a['acft_model']} is not a selected model"
                )
            self.model[a["ev_id"]] = model
        # events in the order of the rule: by model as in MODELS, then NTSB number
        self.events = sorted(
            self.tables["events"], key=lambda e: (MODELS.index(self.model[e["ev_id"]]), e["ntsb_no"])
        )
        self.rank = {e["ev_id"]: i for i, e in enumerate(self.events)}

    def word(self, table: str, column: str, code: str | None) -> str | None:
        """A code as its meaning; a code without a meaning as itself; no code as None."""
        if code is None or not code.strip():
            return None
        return self.codes[f"{table}.{column}"].get(code.strip().upper(), code)

    def rows(self, table: str) -> list[dict]:
        """One table's rows in event order (the raw file's order within an event)."""
        return sorted(self.tables[table], key=lambda r: self.rank[r["ev_id"]])


def _single(value: float | int | None) -> float | int | None:
    """A value of an Access "Single" column (hours, distances) as it was typed: 0.2, not 0.20000000298.

    A single-precision float holds about 7 significant digits, so rounding to 7 restores the typed decimal;
    a whole number is written without ".0" (255, not 255.0).
    """
    if not isinstance(value, float):
        return value
    typed = float(f"{value:.7g}")
    return int(typed) if typed.is_integer() else typed


def _text(value: str | None) -> str:
    """A narrative with "\\n" line endings and no outer whitespace; nothing else changes."""
    return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _most_common(values: list[str | None]) -> str | None:
    # ties go to the alphabetically first value, so the result does not depend on row order
    counts = Counter(v for v in values if v)
    return min(counts, key=lambda v: (-counts[v], v)) if counts else None


def _model_rows(raw: _Raw) -> list[dict]:
    """One row per selected model: its name as the documents give it, and what its kept aircraft say.

    `manufacturer` is the make as the records spell it most often ("CIRRUS DESIGN CORP"), as NHTSA's
    vehicles.csv takes the manufacturer the complaints give most often.
    """
    rows = []
    for make_model in MODELS:
        aircraft = [a for a in raw.tables["aircraft"] if raw.model[a["ev_id"]] == make_model]
        ids = {a["ev_id"] for a in aircraft}
        engines = [e for e in raw.tables["engines"] if e["ev_id"] in ids]
        categories = [raw.word("aircraft", "acft_category", a["acft_category"]) for a in aircraft]
        rows.append(
            {
                "name": model_name(make_model),
                "make": make_model[0],
                "model": make_model[1],
                "manufacturer": _most_common([a["acft_make"] for a in aircraft]),
                "category": _most_common(categories),
                "engine_type": _most_common(
                    [raw.word("engines", "eng_type", e["eng_type"]) for e in engines]
                ),
            }
        )
    return rows


def _time(hhmm: int | None) -> str | None:
    # the source stores the local time as a 4-digit 24-hour number: 1600 -> "16:00"
    return None if hhmm is None else f"{hhmm // 100:02d}:{hhmm % 100:02d}"


def _event_rows(raw: _Raw) -> list[dict]:
    """One row per event, codes as words.

    The air temperature (wx_temp) is left out: the data dictionary gives it in degrees Celsius, but the
    selected values read as Fahrenheit (28 in Alaska in March, 90 in Michigan in May), so its unit is unknown.
    """

    def word(e: dict, column: str) -> str | None:
        return raw.word("events", column, e[column])

    return [
        {
            "ntsb_no": e["ntsb_no"],
            "date": e["ev_date"][:10],
            "time": _time(e["ev_time"]),
            "city": e["ev_city"],
            "state": e["ev_state"],
            "country": e["ev_country"],
            "airport_id": e["ev_nr_apt_id"],
            "airport_name": e["apt_name"],
            "airport_distance_sm": _single(e["apt_dist"]),
            "light_condition": word(e, "light_cond"),
            "weather_condition": word(e, "wx_cond_basic"),
            "wind_speed_kts": e["wind_vel_kts"],
            "gust_kts": e["gust_kts"],
            "highest_injury": word(e, "ev_highest_injury"),
            "fatal_injuries": e["inj_tot_f"],
            "serious_injuries": e["inj_tot_s"],
            "minor_injuries": e["inj_tot_m"],
            "uninjured": e["inj_tot_n"],
            "latitude": e["dec_latitude"],
            "longitude": e["dec_longitude"],
        }
        for e in raw.events
    ]


def _engine(raw: _Raw, engines: list[dict]) -> dict | None:
    """An aircraft's engine as a nested object (staged to dotted columns), None when the record has none.

    Every selected model has one engine; more than one row means the source changed, so the build stops.
    """
    if len(engines) > 1:
        raise ValueError(f"{len(engines)} engine rows for event {engines[0]['ev_id']}; one was expected")
    if not engines:
        return None
    e = engines[0]
    return {
        "manufacturer": e["eng_mfgr"],
        "model": e["eng_model"],
        "type": raw.word("engines", "eng_type", e["eng_type"]),
        "power": e["power_units"],
        "power_unit": raw.word("engines", "hp_or_lbs", e["hp_or_lbs"]),
        "fuel_system": raw.word("engines", "carb_fuel_injection", e["carb_fuel_injection"]),
    }


def _aircraft_records(raw: _Raw) -> list[dict]:
    """One record per aircraft: the selected model as the key to models.csv, the source's own spelling of
    make and model next to it, and its engine nested."""
    engines: dict[str, list[dict]] = defaultdict(list)
    for e in raw.rows("engines"):
        engines[e["ev_id"]].append(e)

    def word(a: dict, column: str) -> str | None:
        return raw.word("aircraft", column, a[column])

    return [
        {
            "ntsb_no": a["ntsb_no"],
            "registration": a["regis_no"],
            "model": raw.model[a["ev_id"]][1],
            "reported_make": a["acft_make"],
            "reported_model": a["acft_model"],
            "series": a["acft_series"],
            "year_built": a["acft_year"],
            "category": word(a, "acft_category"),
            "regulation": word(a, "far_part"),
            "damage": word(a, "damage"),
            "engines": a["num_eng"],
            "seats": a["total_seats"],
            "operator": a["oper_name"],
            "operator_dba": a["oper_dba"],
            "flight_purpose": word(a, "type_fly"),
            "departure_airport": a["dprt_apt_id"],
            "destination_airport": a["dest_apt_id"],
            "engine": _engine(raw, engines[a["ev_id"]]),
        }
        for a in raw.rows("aircraft")
    ]


def _findings(raw: _Raw) -> list[dict]:
    """One record per finding. Cause_Factor and cm_inPc are left out: empty on every selected row."""
    return [
        {
            "ntsb_no": raw.ntsb_no[f["ev_id"]],
            "finding_no": f["finding_no"],
            "finding_code": f["finding_code"],
            "finding": f["finding_description"],
        }
        for f in sorted(raw.rows("Findings"), key=lambda f: (raw.rank[f["ev_id"]], f["finding_no"]))
    ]


def _sequence(raw: _Raw) -> list[dict]:
    """One record per occurrence of an event's sequence; the description already names the flight phase."""
    rows = sorted(raw.rows("Events_Sequence"), key=lambda s: (raw.rank[s["ev_id"]], s["Occurrence_No"]))
    return [
        {
            "ntsb_no": raw.ntsb_no[s["ev_id"]],
            "occurrence_no": s["Occurrence_No"],
            "occurrence_code": s["Occurrence_Code"],
            "occurrence": s["Occurrence_Description"],
            "defining_event": s["Defining_ev"],
        }
        for s in rows
    ]


def _crew_rows(raw: _Raw) -> list[dict]:
    """One row per person of Flight_Crew, with the flight hours of `HOURS` pivoted in from flight_time."""
    hours: dict[tuple[str, int], dict[str, float | int | None]] = defaultdict(dict)
    for t in raw.tables["flight_time"]:
        column = HOURS.get((t["flight_type"], t["flight_craft"]))
        if column:
            hours[(t["ev_id"], t["crew_no"])][column] = _single(t["flight_hours"])

    def word(c: dict, column: str) -> str | None:
        return raw.word("Flight_Crew", column, c[column])

    rows = sorted(raw.rows("Flight_Crew"), key=lambda c: (raw.rank[c["ev_id"]], c["crew_no"]))
    return [
        {
            "ntsb_no": raw.ntsb_no[c["ev_id"]],
            "crew_no": c["crew_no"],
            "category": word(c, "crew_category"),
            "age": c["crew_age"],
            "sex": word(c, "crew_sex"),
            "medical_certificate": word(c, "med_certf"),
            "medical_validity": word(c, "med_crtf_vldty"),
            "injury": word(c, "crew_inj_level"),
            "seat": word(c, "seat_occ_pic"),
            **{column: hours[(c["ev_id"], c["crew_no"])].get(column) for column in HOURS.values()},
        }
        for c in rows
    ]


def _injury_rows(raw: _Raw) -> list[dict]:
    """One row per injury record: a person category, an injury level and the number of people.

    Every source row is kept, the "Total" rows and zero counts too: dropping some would shape the table.
    A "Total" row is not a sum to trust: most have no count, and some that have one disagree with their
    level rows (the README's known properties say how gold questions treat them).
    """
    return [
        {
            "ntsb_no": raw.ntsb_no[i["ev_id"]],
            "person_category": raw.word("injury", "inj_person_category", i["inj_person_category"]),
            "injury_level": raw.word("injury", "injury_level", i["injury_level"]),
            "count": i["inj_person_count"],
        }
        for i in raw.rows("injury")
    ]


def _document(raw: _Raw, make_model: tuple[str, str]) -> str:
    """One Markdown document per model: a title, then one section per event, lowest NTSB number first.

    A section is the factual narrative verbatim, then the probable cause verbatim. The layout follows the
    NHTSA documents (a `##` heading per record, `---` between records); the heading starts with the NTSB
    number, the key linking matches a section to its event by.
    """
    narratives = {n["ev_id"]: n for n in raw.tables["narratives"]}
    sections = []
    for e in raw.events:
        if raw.model[e["ev_id"]] != make_model:
            continue
        n = narratives[e["ev_id"]]
        place = f"{e['ev_city']}, {e['ev_state'] or e['ev_country']}"
        sections.append(
            f"## {e['ntsb_no']} ({e['ev_date'][:10]}, {place})\n{_text(n['narr_accf'])}\n\n"
            f"Probable cause: {_text(n['narr_cause'])}\n"
        )
    return f"# {model_name(make_model)} accidents investigated by the NTSB\n\n" + "\n---\n\n".join(sections)


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _ndjson(records: list[dict]) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"


def build(raw_dir: Path, data_dir: Path) -> None:
    """Write the dataset: models.csv, events.csv, aircraft.json, findings.ndjson, events_sequence.ndjson,
    crew.csv, injuries.csv and documents/<make>_<model>_accidents.md."""
    raw = _Raw(raw_dir)
    (data_dir / "documents").mkdir(parents=True, exist_ok=True)
    _write_csv(data_dir / "models.csv", _model_rows(raw))
    _write_csv(data_dir / "events.csv", _event_rows(raw))
    _write(data_dir / "aircraft.json", _json(_aircraft_records(raw)))
    _write(data_dir / "findings.ndjson", _ndjson(_findings(raw)))
    _write(data_dir / "events_sequence.ndjson", _ndjson(_sequence(raw)))
    _write_csv(data_dir / "crew.csv", _crew_rows(raw))
    _write_csv(data_dir / "injuries.csv", _injury_rows(raw))
    for make_model in MODELS:
        name = f"{slug(model_name(make_model))}_accidents.md"
        _write(data_dir / "documents" / name, _document(raw, make_model))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["fetch", "build"])
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help="where avall.zip is downloaded to (outside the repo)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "fetch":
        fetch(HERE / "raw", args.cache)
    else:
        build(HERE / "raw", HERE / "data")


if __name__ == "__main__":
    main()
