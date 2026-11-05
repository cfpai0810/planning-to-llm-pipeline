-- fact_plan: the promoted plan batch's month cells that hold a figure, one row
-- per version, entity, account and period. Left out: the FY total column (a
-- total, not a month), empty cells, cells with no figure to read, rows the
-- reader flagged (a duplicate key, an unknown entity or account, a row
-- error), members whose names fail the allow-list, and any key not in its
-- dimension. The cell's sheet and address are kept, so a figure can be traced.
CREATE OR REPLACE TABLE fact_plan AS
WITH promoted AS (
    SELECT plan_batch_id FROM gate_promotion ORDER BY number DESC LIMIT 1
)
SELECT
    c.batch_id,
    c.version,
    c.entity,
    c.account,
    c.period,
    c.amount,
    c.sheet,
    c.address
FROM raw_plan_cell AS c
JOIN promoted AS p ON c.batch_id = p.plan_batch_id
JOIN dim_version AS v ON v.version = c.version
JOIN dim_entity AS e ON e.entity = c.entity
JOIN dim_account AS a ON a.account = c.account
JOIN dim_time AS t ON t.period = c.period
WHERE c.column_kind = 'month'
  AND c.amount IS NOT NULL
  AND c.name_allowed
  AND NOT c.duplicate
  AND NOT c.unknown_entity
  AND NOT c.unknown_account
  AND c.row_error IS NULL
ORDER BY v.version_order, e.list_order, a.list_order, c.period;
