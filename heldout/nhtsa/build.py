"""Build the held-out NHTSA dataset: fetch the selected records once, then turn them into pipeline input.

Role: not part of the pipeline. It makes `heldout/nhtsa/data/`, the dataset of the held-out benchmark
(REFACTOR_PLAN R50-R53), which `kg --preset heldout ...` reads like any other data directory.
Design: two separate commands. `fetch` is the only part that uses the network: it asks api.nhtsa.gov
for five vehicles' recalls and complaints, applies the selection rule and saves only the kept records
under `raw/`. `build` is pure: from `raw/` alone it writes `data/`, so a test can rebuild the committed
dataset offline and prove that nothing in it was edited by hand.
Not here: any cleaning of the narratives. They are real owner text and stay verbatim (spacing,
capitals and typos included); the benchmark must measure the system on data it did not shape.
"""

import argparse
import csv
import json
import logging
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
API = "https://api.nhtsa.gov"
# Five makes, one model year each, so that the model name alone identifies a vehicle in this dataset:
# recalls and complaints can then point at `vehicles.csv` through one column, which the profiler's
# single-column key detection can find. Popular models, so every one has recalls and many complaints.
VEHICLES = [
    ("HONDA", "CIVIC", 2016),
    ("NISSAN", "ROGUE", 2017),
    ("TOYOTA", "RAV4", 2019),
    ("FORD", "ESCAPE", 2015),
    ("SUBARU", "OUTBACK", 2019),
]
# The selection rule, fixed before any narrative was read: per vehicle the complaints with the lowest ODI
# numbers (the earliest filed) whose narrative has 40-110 words and that name only this vehicle as the
# product. The word range drops one-line complaints ("see attached") and multi-page ones, which would
# dominate a small corpus; five per vehicle gives about 1,800 words in all, small enough for cheap runs.
COMPLAINTS_PER_VEHICLE = 5
MIN_WORDS, MAX_WORDS = 40, 110


def _write(path: Path, text: str) -> None:
    # "\n" on every platform: the committed files must not depend on the machine that built them
    path.write_text(text, encoding="utf-8", newline="\n")


def _json(value: object) -> str:
    # accents and symbols stay readable in the committed files instead of \u escapes
    return json.dumps(value, indent=1, ensure_ascii=False) + "\n"


def _get(path: str, make: str, model: str, year: int) -> dict:
    """One API response as JSON. Raises `urllib.error.URLError` when the API cannot be reached."""
    query = urllib.parse.urlencode({"make": make, "model": model, "modelYear": year})
    request = urllib.request.Request(f"{API}/{path}?{query}", headers={"User-Agent": "kgbuilder-thesis"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def select_complaints(complaints: list[dict]) -> list[dict]:
    """The complaints the selection rule keeps, lowest ODI number first."""
    eligible = [
        c
        for c in sorted(complaints, key=lambda c: c["odiNumber"])
        if MIN_WORDS <= len(c["summary"].split()) <= MAX_WORDS and len(c.get("products", [])) == 1
    ]
    return eligible[:COMPLAINTS_PER_VEHICLE]


def fetch(raw_dir: Path) -> None:
    """Download every vehicle's recalls (all of them) and selected complaints into `raw_dir`."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    for make, model, year in VEHICLES:
        recalls = _get("recalls/recallsByVehicle", make, model, year)
        complaints = _get("complaints/complaintsByVehicle", make, model, year)
        kept = select_complaints(complaints["results"])
        record = {
            "fetched": date.today().isoformat(),
            "vehicle": {"make": make, "model": model, "model_year": year},
            "recalls": recalls["results"],
            "complaints_total": complaints["count"],
            "complaints": kept,
        }
        target = raw_dir / f"{make.lower()}_{model.lower()}.json"
        _write(target, _json(record))
        log.info(
            "%s: %d recalls, %d of %d complaints",
            target.name,
            len(record["recalls"]),
            len(kept),
            record["complaints_total"],
        )


def _raw_records(raw_dir: Path) -> list[dict]:
    """The raw files in the order of `VEHICLES`, so the output does not depend on directory order."""
    return [
        json.loads((raw_dir / f"{m.lower()}_{n.lower()}.json").read_text("utf-8")) for m, n, _ in VEHICLES
    ]


def _vehicle_rows(records: list[dict]) -> list[dict]:
    # the manufacturer's name is the one the complaints give most often (the API spells it per record)
    return [
        {
            **r["vehicle"],
            "manufacturer": Counter(c["manufacturer"] for c in r["complaints"]).most_common(1)[0][0],
        }
        for r in records
    ]


def _complaint_record(complaint: dict) -> dict:
    """A complaint as a table row: the narrative moves to the documents, the product list to an object.

    The narrative is kept once, in the vehicle's document, so the text path and not a table cell carries
    it. The partial VIN is dropped: nothing needs it. The API wraps the single product in a list; as an
    object it stages to dotted columns (`product.productModel`) that can point at `vehicles.csv`.
    """
    row = {k: v for k, v in complaint.items() if k not in {"summary", "vin", "products"}}
    row["product"] = complaint["products"][0]
    return row


def _document(record: dict) -> str:
    """One Markdown document per vehicle: a title, then one section per complaint narrative.

    The layout follows the furniture reviews (a `##` heading per item, `---` between items), so the
    chunker treats both datasets alike. The title is the chunks' context, the name of what they are about.
    """
    vehicle = record["vehicle"]
    name = f"{vehicle['model_year']} {vehicle['make'].title()} {vehicle['model'].title()}"
    sections = [
        f"## Complaint {c['odiNumber']} (filed {c['dateComplaintFiled']})\n{c['summary'].strip()}\n"
        for c in record["complaints"]
    ]
    return f"# {name} owner complaints to NHTSA\n\n" + "\n---\n\n".join(sections)


def build(raw_dir: Path, data_dir: Path) -> None:
    """Write the dataset (`vehicles.csv`, `recalls.json`, `complaints.ndjson`, `complaints/*.md`)."""
    records = _raw_records(raw_dir)
    (data_dir / "complaints").mkdir(parents=True, exist_ok=True)
    with (data_dir / "vehicles.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["make", "model", "model_year", "manufacturer"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(_vehicle_rows(records))
    recalls = [recall for r in records for recall in r["recalls"]]
    # the shape of the API's own response: one object holding the list of records
    payload = {"Count": len(recalls), "Message": "Results returned successfully", "results": recalls}
    _write(data_dir / "recalls.json", _json(payload))
    lines = [json.dumps(_complaint_record(c), ensure_ascii=False) for r in records for c in r["complaints"]]
    _write(data_dir / "complaints.ndjson", "\n".join(lines) + "\n")
    for r in records:
        name = f"{r['vehicle']['make'].lower()}_{r['vehicle']['model'].lower()}_complaints.md"
        _write(data_dir / "complaints" / name, _document(r))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["fetch", "build"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "fetch":
        fetch(HERE / "raw")
    else:
        build(HERE / "raw", HERE / "data")


if __name__ == "__main__":
    main()
