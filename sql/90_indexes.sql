-- The indexes of the star schema, created after every table is loaded. Each is
-- unique, so a second row for a dimension's key or a fact's grain fails the
-- build instead of passing unseen.
CREATE UNIQUE INDEX dim_version_key ON dim_version (version);
CREATE UNIQUE INDEX dim_time_key ON dim_time (period);
CREATE UNIQUE INDEX dim_entity_key ON dim_entity (entity);
CREATE UNIQUE INDEX dim_account_key ON dim_account (account);
CREATE UNIQUE INDEX fact_plan_grain ON fact_plan (version, entity, account, period);
CREATE UNIQUE INDEX fact_actual_erp_grain ON fact_actual_erp (entity, account, period);
