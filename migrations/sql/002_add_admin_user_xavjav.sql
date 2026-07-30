-- 002_add_admin_user_xavjav.sql
-- Adds the xavjav admin user. The password hash is NOT stored in this file.
-- Generate it from the XAVJAV_PASSWORD env var using the exact call the app
-- uses (werkzeug generate_password_hash, see User.set_password in models.py)
-- and hand it to psql as a variable:
--
--   psql "$DATABASE_URL" \
--     -v password_hash="$(python -c 'import os; from werkzeug.security import generate_password_hash as g; print(g(os.environ["XAVJAV_PASSWORD"]))')" \
--     -f migrations/sql/002_add_admin_user_xavjav.sql
--
-- Note: the users table has no username column; per review decision the
-- literal string 'xavjav' goes in the email column. The login form enforces
-- an email format, so this row cannot log in until that validator is
-- relaxed in a later session.

INSERT INTO users (email, password_hash, role, created_at)
VALUES ('xavjav', :'password_hash', 'admin', now())
ON CONFLICT (email) DO NOTHING;
