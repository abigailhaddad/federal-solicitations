# RFP Watch: federal solicitations and sources-sought notices, checked

**https://rfp-watch.abigailhaddad.com**: each week, every federal services solicitation and sources-sought notice
posted on SAM.gov, with a few picked out by Claude, and lists of planned sole-source awards, named incumbents
(sources-sought notices) and stated values.

**The data** is on Hugging Face: [abigailhaddad/federal-solicitations](https://huggingface.co/datasets/abigailhaddad/federal-solicitations),
as Parquet and CSV. Read its card before using it: it says what was checked, how, and what the values miss.

Every value is read from the notice's own documents and comes with the sentence it was read from and the file
and page that sentence is on. A value is published only if (1) its quote is in the documents, (2) it passes a
rule in code against that quote, and (3) its field passed a sampled check against the documents
([docs/checks](docs/checks)). Empty means "not found", not "not stated": an empty evaluation basis means it was
not found in the notice, not that the notice states none.

## Fields

| Solicitations (services) | Sources-sought notices |
|---|---|
| How offers will be judged (LPTA, tradeoff, ...) | Planned sole-source award, and to whom |
| Evaluation factors | Named incumbent |
| Contract type | Stated value |
| Stated value | |

Fields that were asked for and failed their check are not published (see [docs/checks](docs/checks)).

**Held back** (`HELD` in `src/solicitations/fields.py`): solicitations' relative importance of the factors and
named incumbent are still read, checked and sampled each week, but kept out of the dataset, the post, the site and
the email after the check of the week of 2026-11-09 (relative importance 25 of 30 right, one quote spliced from
two sentences; incumbent 19 of 20, one vendor of earlier drawings named as incumbent).

## How it works

1. **Week** (`solicitations/week.py`): pick the week's notices from SAM.gov's Contract Opportunities CSVs (the
   active file plus the yearly archives). One record per solicitation number, read from its latest posting.
2. **Fetch** (`fetch.py`): download each notice's attachments through the SAM.gov API, keep their text, delete
   the files. Every PDF is read twice, by pypdf and by poppler's `pdftotext` (needed: `brew install poppler` /
   `apt-get install poppler-utils`; the run stops without it).
3. **Extract** (`run.py`, `fields.py`, `prepare.py`): Claude Haiku 5.5 through the Batch API (half price), one
   prompt per track. Packages over 130,000 characters are cut to the passages on these topics, evaluation wording
   first, so every prompt stays under the long-prompt price.
4. **Check** (`check.py`): every quote against the whole package, and a quote on a PDF page against the second
   reading of that page too; every value against its field's rule. When the model left `contract_type` or
   `evaluation_basis` empty, a rule fills it from the package's own stated sentence, if there is exactly one answer.
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
