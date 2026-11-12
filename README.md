# RFP Watch: federal solicitations and sources-sought notices, checked

**https://rfp-watch.pages.dev**: each week, every federal services solicitation and sources-sought notice
posted on SAM.gov, with a few picked out by Claude, and lists of planned sole-source awards, named incumbents and
stated values.

**The data** is on Hugging Face: [abigailhaddad/federal-solicitations](https://huggingface.co/datasets/abigailhaddad/federal-solicitations),
as Parquet and CSV. Read its card before using it: it says what was checked, how, and what the values miss.

Every value is read from the notice's own documents and comes with the sentence it was read from and the file
and page that sentence is on. A value is published only if (1) its quote is in the documents, (2) it passes a
rule in code against that quote, and (3) its field passed a sampled check against the documents
([docs/checks](docs/checks)). Empty means "not found", not "not stated".

## Fields

| Solicitations (services) | Sources-sought notices |
|---|---|
| How offers will be judged (LPTA, tradeoff, ...) | Planned sole-source award, and to whom |
| Relative importance of the factors | Named incumbent |
| Evaluation factors | Stated value |
| Contract type | |
| Stated value | |
| Named incumbent | |

Fields that were asked for and failed their check are not published (see [docs/checks](docs/checks)).

## How it works

1. **Week** (`solicitations/week.py`): pick the week's notices from SAM.gov's Contract Opportunities CSVs (the
   active file plus the yearly archives). One record per solicitation number, read from its latest posting.
2. **Fetch** (`fetch.py`): download each notice's attachments through the SAM.gov API, keep their text, delete
   the files.
3. **Extract** (`run.py`, `fields.py`, `prepare.py`): Claude Haiku 5.5 through the Batch API (half price), one
   prompt per track. Packages over 130,000 characters are cut to the passages on these topics, evaluation wording
   first, so every prompt stays under the long-prompt price.
4. **Check** (`check.py`): every quote against the whole package; every value against its field's rule.
5. **Samples** (`sample.py`, `judge_prompt.md`): 30 values and 10 empty-with-topic-words notices per field, judged
   against the field's definition. Results in `docs/checks/scorecard.json`.
6. **Publish** (`table.py`, `hf.py`): the two tables and the dataset card, every number in it computed.
7. **Post** (`digest.py`, `site.py`): Claude Opus picks up to five notices; code drops a pick whose quote isn't in
   the notice, whose dollar figure isn't the stated value, or that says "sole source", "incumbent" or "LPTA"
   about a notice without that value. Static site, no JavaScript.

```sh
uv sync
uv run pytest
uv run python -m solicitations.week --from 2026-11-02 --to 2026-11-08 ACTIVE.csv FY2027.csv --out work/week.csv
uv run python -m solicitations.fetch work/week.csv --out work/text
uv run python -m solicitations.run work/week.csv --ids work/ids_solicitation.txt --profile solicitation --max-usd 5
uv run python -m solicitations.check work/week.csv work/answers.jsonl --out work/checked.jsonl
uv run python -m solicitations.table work/week.csv work/checked.jsonl --week 2026-11-02 --out work/publish
uv run python -m solicitations.digest work/publish --max-usd 1
uv run python -m solicitations.site --out site/dist
HF_TOKEN=... uv run python -m solicitations.hf publish work/publish
```

Uses the SAM.gov client, text extraction, batch calls and quote matching from
[federal-ja-documents](https://github.com/abigailhaddad/federal-ja-documents) at a fixed commit.

Spotted a wrong value? [Open an issue](https://github.com/abigailhaddad/federal-solicitations/issues/new).
