-- dim_version: the versions of the promoted plan batch, Actual first.
-- A version whose name fails the allow-list has no cell with name_allowed,
-- so it is left out. With nothing promoted the table is empty.
CREATE OR REPLACE TABLE dim_version AS
WITH promoted AS (
    SELECT plan_batch_id FROM gate_promotion ORDER BY number DESC LIMIT 1
)
SELECT DISTINCT
    c.version AS version,
    CAST(CASE c.version WHEN 'Actual' THEN 1 WHEN 'Budget' THEN 2 WHEN 'Forecast' THEN 3
         ELSE 4 END AS INTEGER) AS version_order,
    c.version = 'Actual' AS is_actual
FROM raw_plan_cell AS c
JOIN promoted AS p ON c.batch_id = p.plan_batch_id
WHERE c.version IS NOT NULL AND c.name_allowed
ORDER BY version_order, version;
