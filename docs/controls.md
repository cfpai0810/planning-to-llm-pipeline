# Controls

The controls that check a planning workbook before anything built from it can
be used. This page starts with the member-name allow-list, which the extractor
applies as it reads (`src/names.py`). The other controls arrive with part 1d
and are added here.

## The member-name allow-list

A member name (an account, a parent, an entity or a version) may enter a
prompt only when it passes this rule:

- it is made of Unicode letters, digits, spaces and the characters
  `& . , - / ( ) %` and nothing else;
- it is at most 50 characters long;
- it has at most six words;
- it has no leading or trailing space.

A letter is a character of a Unicode letter category, so "Logística y almacén"
passes; an accent typed as a separate combining mark does not. A digit is a
decimal digit. A space is the plain space: a tab, a line break, a no-break
space or a zero-width character fails. A character is one Unicode code point,
and a word is a run of characters between spaces.

The extractor checks every member name it finds: the codes and names of the
accounts and entities on Lists, the account parents, the versions of the plan
blocks, and any line label that is not on Lists. A name that fails is recorded
with its reasons, as fixed codes (`characters`, `length`, `words`, `space`,
`empty`), and the member is landed raw in the warehouse. It is excluded from
the star schema and from every prompt, and the break that reports it says so.

### What the allow-list cannot do

The allow-list narrows the route into a prompt; it does not close it. A short
sentence in plain English, such as "Ignore the rules please", is four ordinary
words and passes any character list. What stops such a name from steering the
model sits after it, in code: names reach the model only as JSON-encoded data,
never as instructions; the model can only return a specification that matches
a fixed schema; Python checks that specification against the finance-owned
metric registry before anything runs; and a refusal uses fixed wording. The
evaluation's golden set carries that exact label, so the claim is measured,
not assumed.

## The Control tab

The workbook's own check cells are not read: the controls compute what they
compare from the extracted cells, and the sample has no check cells.
