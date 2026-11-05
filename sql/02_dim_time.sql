-- dim_time: the fiscal calendar of FY2026, one row per month. The fiscal year
-- equals the calendar year, so the fiscal quarter is the calendar quarter.
CREATE OR REPLACE TABLE dim_time AS
SELECT
    printf('2026-%02d', m) AS period,
    CAST(2026 AS INTEGER) AS fiscal_year,
    CAST((m + 2) // 3 AS INTEGER) AS fiscal_quarter,
    'Q' || CAST((m + 2) // 3 AS VARCHAR) AS quarter,
    CAST(m AS INTEGER) AS month_number,
    monthname(make_date(2026, CAST(m AS INTEGER), 1)) AS month_name,
    make_date(2026, CAST(m AS INTEGER), 1) AS first_day,
    last_day(make_date(2026, CAST(m AS INTEGER), 1)) AS last_day
FROM range(1, 13) AS months(m)
ORDER BY period;
