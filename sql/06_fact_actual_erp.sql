-- fact_actual_erp: the promoted ledger batch's lines, one row per entity,
-- account and period, kept apart from fact_plan so the tie-out stays visible.
-- amount is in presentation sign (revenue positive): the ledger amount times
-- the account's factor in dim_account; ledger_amount is the figure as landed.
-- Left out: lines with a row error or no amount, duplicated keys, and codes
-- that are not in the dimensions of the promoted plan batch (unknown members,
-- names that fail the allow-list, accounts the ledger does not hold).
CREATE OR REPLACE TABLE fact_actual_erp AS
WITH promoted AS (
    SELECT ledger_batch_id FROM gate_promotion ORDER BY number DESC LIMIT 1
)
SELECT
    l.batch_id,
    l.entity,
    l.account,
    l.period,
    CAST(l.amount * a.sign_to_presentation AS DECIMAL(38, 12)) AS amount,
    l.amount AS ledger_amount,
    l.line_number
FROM raw_ledger_line AS l
JOIN promoted AS p ON l.batch_id = p.ledger_batch_id
JOIN dim_entity AS e ON e.entity = l.entity
JOIN dim_account AS a ON a.account = l.account AND a.sign_to_presentation IS NOT NULL
JOIN dim_time AS t ON t.period = l.period
WHERE l.row_error IS NULL
  AND l.amount IS NOT NULL
  AND NOT l.duplicate
ORDER BY e.list_order, a.list_order, l.period;
