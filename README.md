# GENIUS

Extract, validate, and classify climate policies from government PDFs across cities and countries.

## Quick start

1. Set your API key: `export OPENAI_API_KEY=...`
2. Open `notebooks/dspy_pipeline_v4.ipynb`
3. Edit the `BATCH` list (cell ~11) to add cities — each entry needs a `DocumentMetadata` and either a `markdown_path` or `pdf_path`
4. Run all cells top to bottom

Output lands in `notebooks/outputs/{city_key}/`. The cross-city aggregate is at the repo root: `all_cities_kept_classified_policies_final.csv`.

## Repo layout

```
notebooks/
  dspy_pipeline_v4.ipynb   ← main pipeline notebook
  utils/                   ← all pipeline modules (chunking, extraction, validation, classification, exports)
  outputs/                 ← per-city results (csv, traces, json)

docs/
  pipeline_architecture_v4.md  ← how the pipeline works
  cities/                      ← pre-converted city markdowns

pdfs/                      ← source PDFs
```

## Adding a city

Add an entry to `BATCH` in the notebook:

```python
{
    "metadata": DocumentMetadata(country="...", state_or_province="...", city="..."),
    "markdown_path": "../docs/cities/my_city.md",   # preferred
    # "pdf_path": "../pdfs/my_city.pdf",             # fallback if no markdown
}
```

If supplying a PDF, Docling converts it on first run and caches the result.

## Architecture

See `docs/pipeline_architecture_v4.md` for a full description of the 9-step pipeline and design decisions.
