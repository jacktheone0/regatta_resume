-- 003_select_future_or_empty_regattas.sql
-- READ-ONLY. Surfaces ClubSpot regatta rows left behind by earlier runs,
-- for eyeballing before any cleanup is agreed. No DELETE here.
--
-- Run: psql "$DATABASE_URL" -f migrations/sql/003_select_future_or_empty_regattas.sql
--
-- Until the scraper stopped creating regatta rows before it had results,
-- every regatta a run ever looked at got a permanent row -- including
-- upcoming events fetched before the startDate bound existed. The scraper
-- now only stores a regatta once it has results, but nothing removed the
-- rows that were already there.
--
-- Reasons flagged:
--   starts_in_future   start_date is after today
--   no_results         no rows in results point at this regatta

WITH candidates AS (
    SELECT
        r.id,
        r.external_id,
        r.name,
        r.location,
        r.start_date,
        r.end_date,
        r.created_at,
        (SELECT count(*) FROM results x WHERE x.regatta_id = r.id) AS result_count,
        array_remove(ARRAY[
            CASE WHEN r.start_date > current_date THEN 'starts_in_future' END,
            CASE WHEN NOT EXISTS (SELECT 1 FROM results x WHERE x.regatta_id = r.id)
                 THEN 'no_results' END
        ], NULL) AS reasons
    FROM regattas r
)
SELECT id, external_id, name, location, start_date, end_date,
       result_count, reasons, created_at
FROM candidates
WHERE cardinality(reasons) > 0
ORDER BY start_date DESC, id;

-- Counts by reason (a row can carry both)
WITH candidates AS (
    SELECT
        r.id,
        r.start_date > current_date AS starts_in_future,
        NOT EXISTS (SELECT 1 FROM results x WHERE x.regatta_id = r.id) AS no_results
    FROM regattas r
)
SELECT
    count(*) FILTER (WHERE starts_in_future)               AS starts_in_future,
    count(*) FILTER (WHERE no_results)                     AS no_results,
    count(*) FILTER (WHERE starts_in_future OR no_results) AS candidate_rows,
    count(*)                                               AS all_regattas
FROM candidates;
