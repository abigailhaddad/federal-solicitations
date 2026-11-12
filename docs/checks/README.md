# Check results

One folder per field (`<track>__<field>`), from the week of 2026-11-02:

- `values_sample.json`: a random sample of kept values (30, or all of them when fewer), each with its quote,
  the document text around the quote, the SAM.gov link and the field's written definition.
- `values_verdicts.json`: one verdict per item: `correct`, `wrong` or `unclear`, with the document words it
  rests on.
- `blanks_sample.json` / `blanks_verdicts.json`: 10 notices where the field is empty but the documents have words
  on the topic: `missed` (the text states a value), `correct_blank` or `unclear`. This measures what the field
  misses; the contexts are partial, so `correct_blank` means only that this text doesn't state it.

`scorecard.json` has the tallies; the dataset card's table is built from it.

**Who judged.** Claude subagents, one per field, each given only the sample file, the field's definition and
`src/solicitations/judge_prompt.md`. Not a person, and not a census.

**Bar.** About 28 of 30 correct. Two fields fell short and are not published: `sources_sought__set_aside_considered`
(6 of 30: nearly every notice has "responses will determine whether this is set aside" boilerplate) and
`sources_sought__planned_strategy` (22 of 30).

**Rounds.** Values were sampled again whenever the answers or rules behind a field changed, so each folder's
sample matches the published values: `evaluation_basis`, `relative_importance` and `sole_source_intent` after the
second prompt version; `evaluation_factors`, `contract_type` and `incumbent` after the final re-run (2026-11-12);
`stated_value` after the per-contract, minimum and A-E construction rules (2026-11-12), which were written from the
previous sample's errors, so a fresh sample was drawn to judge them.

The proof files hold document text from SAM.gov notices as posted, including contact details that are in the
public notices.
