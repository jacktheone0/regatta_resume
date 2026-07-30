-- 001_select_faulty_sailors.sql
-- READ-ONLY. Surfaces candidate bad rows in sailors for eyeballing.
-- No DELETE in this session; the predicate gets agreed on after review.
--
-- Run: psql "$DATABASE_URL" -f migrations/sql/001_select_faulty_sailors.sql
--
-- Reasons flagged (derived from the schema and how scraper.py fills rows):
--   blank_name            name is NULL or only whitespace
--   normalization_drift   name_normalized is NULL or != lower(btrim(name))
--   junk_characters       name contains digits or scrape artifacts (| / @ #)
--   single_token_name     no space in trimmed name (not a full "First Last")
--   duplicate_normalized  another sailors row shares the same name_normalized
--   orphan_no_results     zero rows in results AND profile is unclaimed

WITH candidates AS (
    SELECT
        s.id,
        s.name,
        s.name_normalized,
        s.home_club,
        s.is_claimed,
        s.created_at,
        (SELECT count(*) FROM results r WHERE r.sailor_id = s.id) AS result_count,
        array_remove(ARRAY[
            CASE WHEN s.name IS NULL OR btrim(s.name) = ''
                 THEN 'blank_name' END,
            CASE WHEN s.name_normalized IS NULL
                      OR s.name_normalized <> lower(btrim(s.name))
                 THEN 'normalization_drift' END,
            CASE WHEN s.name ~ '[0-9|/@#]'
                 THEN 'junk_characters' END,
            CASE WHEN btrim(coalesce(s.name, '')) <> ''
                      AND position(' ' IN btrim(s.name)) = 0
                 THEN 'single_token_name' END,
            CASE WHEN EXISTS (SELECT 1 FROM sailors d
                              WHERE d.name_normalized = s.name_normalized
                                AND d.id <> s.id)
                 THEN 'duplicate_normalized' END,
            CASE WHEN NOT coalesce(s.is_claimed, false)
                      AND NOT EXISTS (SELECT 1 FROM results r
                                      WHERE r.sailor_id = s.id)
                 THEN 'orphan_no_results' END
        ], NULL) AS reasons
    FROM sailors s
)
SELECT id, name, name_normalized, home_club, is_claimed,
       result_count, reasons, created_at
FROM candidates
WHERE cardinality(reasons) > 0
ORDER BY reasons, name_normalized NULLS FIRST, id;

-- Count of candidate rows (same predicate as above).
WITH candidates AS (
    SELECT
        s.id,
        array_remove(ARRAY[
            CASE WHEN s.name IS NULL OR btrim(s.name) = ''
                 THEN 'blank_name' END,
            CASE WHEN s.name_normalized IS NULL
                      OR s.name_normalized <> lower(btrim(s.name))
                 THEN 'normalization_drift' END,
            CASE WHEN s.name ~ '[0-9|/@#]'
                 THEN 'junk_characters' END,
            CASE WHEN btrim(coalesce(s.name, '')) <> ''
                      AND position(' ' IN btrim(s.name)) = 0
                 THEN 'single_token_name' END,
            CASE WHEN EXISTS (SELECT 1 FROM sailors d
                              WHERE d.name_normalized = s.name_normalized
                                AND d.id <> s.id)
                 THEN 'duplicate_normalized' END,
            CASE WHEN NOT coalesce(s.is_claimed, false)
                      AND NOT EXISTS (SELECT 1 FROM results r
                                      WHERE r.sailor_id = s.id)
                 THEN 'orphan_no_results' END
        ], NULL) AS reasons
    FROM sailors s
)
SELECT count(*) AS candidate_bad_rows
FROM candidates
WHERE cardinality(reasons) > 0;
