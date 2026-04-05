# Output Files Guide

This folder now contains three different all-city outputs with different purposes.

## Master Files

`all_cities_classified_policies.csv`
- The full master classification table.
- Contains all classified rows, including rows with `climate_screen = exclude`.
- Best for auditing the complete classification output.

`all_cities_valid_policies.csv`
- The slim kept-only climate-policy dataset.
- Excluded rows have already been removed by the final deterministic filter.
- Best for a presentation-friendly final dataset.

`all_cities_excluded_policies_trace.csv`
- The master audit table of excluded rows only.
- Useful for reviewing what was screened out and why.

`all_cities_kept_classified_policies.csv`
- The rich kept-only master classification table.
- Contains the same wide classification fields as `all_cities_classified_policies.csv`, but only for rows where `climate_screen != exclude`.
- Best when you want detailed classification columns without excluded rows.

## Per-City Files

`<City>/classified_policies.csv`
- Full per-city classified output.
- Includes excluded rows.
- Wide table with primary, secondary, screen, and enrichment fields.

`<City>/combined_policies.csv`
- Slim per-city kept dataset after the final deterministic filter.
- Excluded rows are not included.

`<City>/excluded_policies_trace.csv`
- Per-city excluded rows only.
- Use this to inspect removals for a single city.

`<City>/policy_trace_lookup.csv`
- Maps kept rows in `combined_policies.csv` to their trace JSON files.

`<City>/policy_traces/*.json`
- One JSON trace per kept row.
- Detailed classification trace for each kept policy.

`<City>/trace_classification.json`
- Per-city classification trace snapshot from the wide classified output.
- Mainly useful as a diagnostic or export artifact.

## Registry File

`mechanism_registry.json`
- Stage 2 mechanism registry.
- Stores one entry per canonical mechanism with its shared primary classification.

## Rule Of Thumb

- Use `all_cities_valid_policies.csv` if you want the final slim kept dataset.
- Use `all_cities_kept_classified_policies.csv` if you want the final kept dataset with rich classification columns.
- Use `all_cities_classified_policies.csv` if you want everything, including excluded rows.
- Use `all_cities_excluded_policies_trace.csv` if you want only what was removed.
