# Held-out dataset: NHTSA vehicle recalls and owner complaints

The second dataset of the thesis, used only to test whether the system generalises (REFACTOR_PLAN
R50-R53). The pipeline is **frozen** for it: no prompt, threshold or model is changed because of what it
does on this data. The furniture data (`data/`) is where the system was developed; this is where it is
tested.

## Source and licence

Records of the US National Highway Traffic Safety Administration, fetched from the public API
(`https://api.nhtsa.gov/recalls/recallsByVehicle`, `.../complaints/complaintsByVehicle`) on the date in
each `raw/*.json` file. Works of the US federal government are in the public domain. The complaint
narratives are written by vehicle owners and published by NHTSA; the partial VIN in each complaint is
not kept.

## What was selected, and how

- Five vehicles of five makes, one model year each: 2016 Honda Civic, 2017 Nissan Rogue, 2019 Toyota
  RAV4, 2015 Ford Escape, 2019 Subaru Outback. One year per model, so the model name identifies a vehicle.
- Every recall of those vehicles (29).
- Per vehicle, 5 complaints by a rule fixed before any narrative was read: the lowest ODI numbers (the
  earliest filed) whose narrative has 40-110 words and that name only this vehicle as the product (25
  complaints, 1,974 words of narrative).

## Files

`raw/` holds the selected records as fetched. `data/` is the pipeline input, written from `raw/` by
`build.py` and never edited by hand (a test rebuilds it and compares):

| File | Format | Content |
|---|---|---|
| `data/vehicles.csv` | CSV | make, model, model year, manufacturer |
| `data/recalls.json` | nested JSON, the API's response shape | component, summary, consequence, remedy per recall |
| `data/complaints.ndjson` | one JSON object per line, the product as a nested object | date, component, crash, fire, injuries per complaint |
| `data/complaints/<make>_<model>_complaints.md` | Markdown | the owners' narratives, one section per complaint, verbatim |

The narratives appear only in the documents, not in `complaints.ndjson`, so the text path carries them.

```
uv run python heldout/nhtsa/build.py fetch   # network: re-download raw/ (the API grows: new complaints)
uv run python heldout/nhtsa/build.py build   # offline: raw/ -> data/
uv run kg --preset heldout run --goal "..."  # a comprehensive run: Claude Code asks first
```

Gold set: `tests/gold/heldout_nhtsa_gold.json` (R51), written from these files before any pipeline
output for them existed.
