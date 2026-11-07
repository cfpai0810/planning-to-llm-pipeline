-- dim_version: the three versions the star schema admits, Actual first, built
-- the way dim_time is: fixed here, whatever the promoted plan batch holds
-- (part 2a). fact_plan joins on this table, so a block read under any other
-- version is landed raw and never built; the structure control reports it
-- (version_not_admitted). The table is the same before and after a promotion.
CREATE OR REPLACE TABLE dim_version AS
SELECT
    v.version,
    CAST(v.version_order AS INTEGER) AS version_order,
    v.version = 'Actual' AS is_actual
FROM (VALUES ('Actual', 1), ('Budget', 2), ('Forecast', 3)) AS v(version, version_order)
ORDER BY version_order;
