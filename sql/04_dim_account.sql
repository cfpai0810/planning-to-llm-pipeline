-- dim_account: the accounts on the Lists of the promoted plan batch whose code,
-- name and parent pass the allow-list, with the section, the sign convention
-- (the ledger sign, and the factor that turns it into the presentation sign:
-- a Credit account times -1, a Debit account times 1, none for a line the
-- ledger does not hold), the polarity, the parent, the unit and the kind of
-- line, flow or stock. With nothing promoted the table is empty.
CREATE OR REPLACE TABLE dim_account AS
WITH promoted AS (
    SELECT plan_batch_id FROM gate_promotion ORDER BY number DESC LIMIT 1
)
SELECT
    a.code AS account,
    a.name AS account_name,
    a.parent,
    a.section,
    a.ledger_sign,
    CAST(CASE a.ledger_sign WHEN 'Credit' THEN -1 WHEN 'Debit' THEN 1 END AS INTEGER)
        AS sign_to_presentation,
    a.polarity,
    a.unit,
    lower(a.kind) AS line_kind,
    a.list_row AS list_order
FROM raw_list_account AS a
JOIN promoted AS p ON a.batch_id = p.plan_batch_id
WHERE a.code IS NOT NULL AND a.name_allowed
ORDER BY list_order;
