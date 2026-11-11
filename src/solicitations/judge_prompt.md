You are checking values extracted from federal contracting notices (solicitations, RFQs, RFIs,
sources-sought notices) against the document text. Only write the output file named below.

Input: {sample_path}. It has `field`, `definition` (what the dataset claims the value is) and `items`.

{mode_instructions}

Judge only against the definition and the document text in `context`. Don't give credit for "close".
A clause title in a clause list states nothing by itself. Something stated for a different acquisition,
a past contract or FAR in general does not count. If the context doesn't let you decide, say "unclear"
and why.

Write {verdicts_path}: a JSON list with one object per item, in input order, with "notice_id",
"verdict" and "reason" (quoting the short document words the verdict rests on). Then reply with the
tally and one line per item that is not "correct" (or, for blanks, every "missed").

---
MODE values: Each item has `value` (with its `quote`). Verdict "correct" if the document states this
for this acquisition and the value matches the definition and the document; "wrong" if it doesn't (wrong
category, wrong number, not this acquisition, a clause title, a reservation of rights read as a
decision, ...); "unclear" otherwise.

MODE blanks: Each item is a notice where the dataset has NO value for this field, with document text
around words on the topic. Verdict "missed" if the context states a value that fits the definition
(say what it is and quote it), "correct_blank" if it doesn't (the words are about something else, a
clause title, another acquisition), "unclear" otherwise. The context is partial, so "correct_blank"
means only that this text doesn't state it.
