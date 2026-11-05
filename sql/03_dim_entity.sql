-- dim_entity: the entities on the Lists of the promoted plan batch whose code
-- and name pass the allow-list. With nothing promoted the table is empty.
CREATE OR REPLACE TABLE dim_entity AS
WITH promoted AS (
    SELECT plan_batch_id FROM gate_promotion ORDER BY number DESC LIMIT 1
)
SELECT
    e.code AS entity,
    e.name AS entity_name,
    e.region,
    e.list_row AS list_order
FROM raw_list_entity AS e
JOIN promoted AS p ON e.batch_id = p.plan_batch_id
WHERE e.code IS NOT NULL AND e.name_allowed
ORDER BY list_order;
