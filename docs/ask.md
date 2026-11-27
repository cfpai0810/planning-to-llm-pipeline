# The question path

A question in words, answered under a role and recorded. The model does one
job: it turns the question into a specification, or refuses. It never writes
SQL and never calculates, and nothing it writes is shown to a reader except
narration that passed the checks. Python validates the specification,
compiles it into one SELECT, runs it, narrates the answer and writes the
audit record. The web pages are still stubs: the question path is used from
the command line, with your own key. The governed layer behind it is in
[governed_layer.md](governed_layer.md), the terms in
[glossary.md](glossary.md).

## Asking a question

```bash
python main.py ask "What was revenue by entity for the year to June, against Budget?" --pair sample --role group_controller --acknowledge-all-as "A. Reviewer" --promote-as "P. Promoter"
```

- **The key.** `ask` reads `ANTHROPIC_API_KEY` from the environment before
  anything else, and from nowhere else. With no key, or one of white space
  alone, it prints one plain line and exits 1: nothing is built and nothing
  is sent. It never judges a key by its look (only the API can say whether a
  key works), and it never prints or writes it.
- **The client** comes from the app's client seam,
  `streamlit_app/lib/claude_client.py`: no retry (`max_retries=0`), no
  address and no timeout of its own, so the SDK's default timeout applies.
- **A temporary folder of its own.** It builds the template warehouse in a
  fresh temporary folder (prefix `p7_ask_`) and opens its session under a
  root inside it (F26), so it never touches another process's sessions. It
  may acknowledge every break of the pair and promote it as the people named,
  as `query` does. The folder is removed on every path: an answer, a refusal,
  a failed call and a fault alike.
- **The audit folder** is never the temporary one. The line goes to
  `audit.jsonl` in `audit_log` in the current directory, unless `--audit
  <folder>` names another; `ask` prints the full path.
- **What it prints,** in this order: the question as received, JSON-encoded,
  so a question refused for a control character cannot drive the terminal;
  the outcome's sentence, the failed call's line, or the canonical
  specification and the answer as `query` prints it (the SQL and its hash,
  the figures, the coverage and gaps, the provenance); the narration shown and
  any flags (kind, text and reason); for each model call its purpose,
  outcome, the model that served it, the stop reason and the tokens; the run
  id; and where the audit line went. A withheld model text is not printed,
  nor is a request id: both are in the audit line.
- **Exit codes:** an answer exits 0, whatever narration was shown; an outcome
  or a failed call exits 1; a wrong option exits 2; no key exits 1.
- **The console.** A character the console's encoding cannot hold is written
  as a backslash escape, so a reply holding, say, the minus sign (U+2212)
  never stops a print on a console that writes another code page.

## The steps

`src/ask.py` is the one way in, for the command line now and the pages later:

1. the question: text of 1 to 500 characters once trimmed, with no control
   character but newline and tab, no format character and no surrogate
   (`narration.unreadable`, the one rule for a question and for model
   wording); anything else is `question_invalid`, before anything is sent;
2. the role: a role of the wrong shape is a fault in the caller's code and
   raises;
3. the role's member lists: `nothing_promoted` when nothing is;
4. the request: the schema and the prompt;
5. one call to the model, whatever comes back, with no retry;
6. the reply mapped: a refusal reason to its outcome, a specification to the
   session, where it is validated, compiled and run as in Phase 2, and
   anything else to `unreadable_reply`;
7. narration, of an answer with figures alone;
8. the audit record, written last;
9. the result.

It never raises for anything a visitor or the model can cause. A session that
ends mid-question is `session_ended`, and a record that cannot be written is
`audit_unavailable`: then no figure and no narration leave.

## The schema

The reply's JSON schema is built from the registry and the promoted pair's
versions, the same bytes for every role (`schema_sha256`
`4eeb3f6a918e3f55d6c5123a53a81e70b1937e897406de3a2be0bba0fcb15a19`). Its
objects are closed and every property is required; only `type`,
`properties`, `required`, `additionalProperties`, `enum`, `items` and `anyOf`
appear, with no limits, and three fields can be null. A reply holds
`refusal` (null, or a reason), `specification` (null, or the eight fields)
and `asks_for_a_cause`.

Member names are plain strings in the schema, not enums. That keeps the
grammar small, and it lets an entity controller's question about another
entity reach validation with the word it used: a real name and a made-up one
both come back as the one fixed `entity_not_permitted`, so the reply never
tells whether a member exists. Validation judges every name.
`python main.py schema --out <folder>` writes the schema and each role's
system text, with their hashes.

## The prompt

The system text is a fixed template filled with data blocks: the metric
catalogue, the pair's versions, and the role's member lists alone. It tells
the model its one job, to reply with JSON matching the schema, never to write
SQL or state a figure, to copy member names as written, the period grammar,
and when to refuse. A question that names no version is asked in Actual; a
relative date ("last month") is `needs_clarification`, because the model
does not know the date. The user message is the question as sent, and nothing
else. The same question, role, registry and pair give the same request bytes;
the record keeps `system_sha256`, `schema_sha256` and `request_sha256`. For an
entity controller the system text and the schema hold no other entity's
code, name or region.

## Refusals and failed calls

A refusal is a reason the model gives in place of a specification, and an
outcome with one fixed sentence:

| Reason | Sentence |
|---|---|
| `out_of_scope` | That is not a question this pipeline can answer: it answers questions about the plan's figures. |
| `needs_clarification` | The question is ambiguous. Say which metric, version, entity and period you mean. |
| `change_not_possible` | This pipeline answers questions. It does not change data, definitions or access. |
| `currency_not_supported` | All figures are in euros. Conversion to another currency is not available. |

Validation's own outcomes (`unknown_metric`, `entity_not_permitted` and the
rest) are in [governed_layer.md](governed_layer.md). A failed call is a code
of `src/errors.py`, shown as its one line: `model_refused` (the model stopped
on refusal), `reply_cut_off` (it stopped at the token limit),
`unreadable_reply` (the reply is not one specification or refusal),
`key_rejected`, `key_forbidden`, `model_not_found`, `bad_request`,
`rate_limited`, `service_unavailable` and `billing_problem`. There is one
specification call per question and no retry, whatever comes back.

## Narration and the checks

An answer with figures is narrated by the template by default: Python's own
sentences, each number beside its figure's name and each direction word in
brackets in its variance's clause ("... was -212,634 EUR (adverse)"). It
passes the checks with strict naming over every answer shape of the sample
pair.

With `--narrate model` the model words the answer in one more call, with no
schema, from the figures as shown, their names and the coverage. Its text is
shown only when the checks give it no flag: the numeric check (each number
against its figure, a written sign against the figure's sign), the direction
check (up or down, favourable or adverse, profit or loss, offset), both with
strict naming, so each number and direction word must share its clause with
its figure's name, and the causal words ("because", "due to" and the rest),
since the plan holds no causes. The direction check is a vocabulary check: it
reads the words on its list and judges nothing else. A flag withholds the
text: the template is
shown with "The model's wording was not shown because it did not match the
computed figures." A failed narration call, or a reply that is not read,
shows the template with "Model wording was not available." A reply is not
read, in this order, when it holds more than one text block, is white space
alone, is longer than 4,000 characters, or holds a control, format or
surrogate character. A question that asks why gets one fixed sentence after
the narration: "The plan holds no causes, so this shows what moved and by how
much, not why."

## The audit

Every question leaves one line, refusals and failures included, written after
everything else: `audit.jsonl` in the audit folder, appended to. The line is
the canonical JSON of the record (sorted keys, no spaces, ASCII escapes only)
with its `record_sha256`, the SHA-256 of the record without that field. The
record's fields are a closed set: the run id, the time, the role, the
question (cut at 2,000 characters, beside its full length), the provenance,
the prompt's hashes, each model call, the model's reply as received, the
canonical specification, the outcome, the SQL and its hash, the result hash,
the row count, truncation, coverage and gaps, the engine's message of an
`engine_error` (cut at 2,000 characters, beside its full length), whether the
question asked why, and the narration. It never holds the key, a request
header or an exception's text. `audit.verify` reads every line and reports
what is wrong with each.

## Replay

```bash
python main.py replay audit_log/audit.jsonl 0123456789abcdef0123456789abcdef
```

`replay` reads the file and verifies it, finds the one line with that run id,
and finds the pair whose two file hashes are the record's. It builds the
template in a temporary folder of its own (prefix `p7_replay_`), acknowledges
every break of that pair as "Replay", promotes it as "Replay", asks the
record's specification again under the record's role and prints one state:
`same` or `different`, with the outcome, the SQL hash and the result hash of
the record and of the replay; `not_comparable`, with the kinds that differ
(the planning workbook's hash, the ledger extract's hash, the registry, the
role view); or `not_replayable`, with the reason, for a record that holds no
specification. A different engine version is printed, not refused. A file
that does not verify, a run id in no line or in more than one, and a record
no pair matches are each one plain line, before anything is built. `same`
exits 0 and every other state 1. `replay` reads no key, makes no model call,
writes no audit line and leaves the file as it was.

## Live checks

The owner ran two live checks against the real API on 27 November 2026,
each with the owner's own key and a script kept outside the repository.

- **The question step,** at commit 83b0466: nine questions, answered or
  refused as expected, 9 of 9.
- **Narration,** at commit 267ec35: six questions with the model's wording.
  The model's text was shown 4 times and withheld 2 times. Both withheld texts
  were true, and both were withheld for the comma pattern: a direction word
  after a comma ("..., adverse") stands in a clause with no figure name, so
  strict naming flags it.
- **The model served** every call: `claude-sonnet-5-5`.
- **The results files** are kept outside the repository.

## Limits

### The checks

- A negative level written with no sign passes: "EBIT was 500,000 EUR" passes
  for a loss of 500,000, unless a direction word says otherwise.
- Brackets and an en dash (U+2013) before the number are not signs:
  "(118,992)" and the number after an en dash are read by their size.
- Numbers spelt out in words are not read ("Two rows are not shown", seen in
  the live check).
- Only vocabulary is checked: the numbers written in digits, with their signs
  and units, the direction words and the causal words. Any other wording is
  not checked, advice and a name that is not among the rows included.
- The narration's system text asks for three sentences at most; the count is
  not enforced.
- Strict naming withholds a true direction word written after a comma
  ("..., adverse"), as the live check saw in 2 of 6.

### Model wording

- Model wording longer than 4,000 characters, or holding a control, format or
  surrogate character (a carriage return included), is not read: the
  template is shown instead.
- An empty second text block counts as one block, because the text joined is
  the first block's.
- For a reply of two text blocks, the narration call's entry in the record
  says `ok`, while the narration's failure is `unreadable_reply`.
- A flag's reason quotes the computed figure with all its decimal places
  ("-212,634.000000000000").

### The question

- A question that names no version is asked in Actual, and a relative date
  ("last month") is asked back as `needs_clarification`.
- The leak search reads entity codes in capitals alone, so the ordinary word
  "at" in the registry's headcount definition, in the ES and DE controllers'
  system texts, is not read as Austria's code; a test pins it as the one such
  word.
- The schema's fixed words (a dimension, a comparison, a period's kind, a
  sort's field and direction) are matched exactly (N2); a metric, a version
  and a member are matched in any case.

### The audit

- `audit.verify` detects an altered line, but not a deleted line, two lines
  swapped, or a line written twice. The hash has no key, so whoever can write
  the file can rewrite a line with a new hash: the audit is detective, not
  preventive.
- Each line is flushed and not synced to the disk (no fsync). The Release
  phase decides where the file lives; on a host whose disk is temporary, it
  lasts until the host restarts.
- Two processes writing one audit file are not coordinated; a lock covers the
  threads of one process.
- The model's reply is kept whole, so a line can reach tens of kilobytes, and
  more when non-ASCII text is escaped; the call's 16,000 output tokens bound
  it. The question is cut at 2,000 characters, beside its full length.
- A failed call keeps no request id.

### Calls and limits

- The values of a filter are not capped (F30): validation accepts as many as
  a reply holds, and the reply's size bounds them.
- The SDK's default timeout applies to a call outside the live checks; the
  web phase sets one.
- The SDK also reads `ANTHROPIC_BASE_URL` and `ANTHROPIC_LOG` from the
  environment. With `ANTHROPIC_BASE_URL` set, the key and the question go to
  that address and not to Anthropic's, so unset both before asking, as the
  live checks' launchers did. The web phase pins the address in the client
  seam, with the timeout.
