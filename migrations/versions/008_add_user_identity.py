"""Add user identity fields, email verification, and admin flag

Revision ID: 008_add_user_identity
Revises: 007_regatta_catalog
Create Date: 2026-10-23

Stores the name and email the sailor gives us when they claim a profile, plus
the email-verification state that gates publishing a resume. Also adds the
`is_admin` flag -- until now every logged-in user could reach /admin and the
scraper controls.

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '008_add_user_identity'
down_revision = '007_regatta_catalog'
branch_labels = None
depends_on = None

# (name, type, kwargs) -- nullable text/int columns need no server_default
IDENTITY_COLUMNS = (
    ('full_name', sa.String(length=200), {}),
    ('display_name', sa.String(length=200), {}),
    ('grad_year', sa.Integer(), {}),
    ('primary_school', sa.String(length=200), {}),
    ('profile_completed_at', sa.DateTime(), {}),
    ('email_verify_token', sa.String(length=64), {}),
    ('email_verify_sent_at', sa.DateTime(), {}),
)

# Non-null booleans need a server_default so existing rows get a value
FLAG_COLUMNS = ('email_verified', 'is_admin')


def false_default():
    """
    Server default for a NOT NULL boolean column, per dialect.

    A plain `server_default='false'` string gets quoted into `DEFAULT 'false'`,
    which SQLite stores as the TEXT 'false' -- and SQLAlchemy's Boolean reads
    any non-empty string as True, so every existing row would come back
    is_admin=True. sa.text() emits the literal unquoted: `false` for Postgres,
    `0` for SQLite.
    """
    return sa.text('false' if op.get_bind().dialect.name == 'postgresql' else '0')


def upgrade():
    for name, type_, kwargs in IDENTITY_COLUMNS:
        op.add_column('users', sa.Column(name, type_, nullable=True, **kwargs))

    for name in FLAG_COLUMNS:
        op.add_column('users', sa.Column(
            name, sa.Boolean(), nullable=False, server_default=false_default()
        ))

    op.create_index('ix_users_email_verify_token', 'users', ['email_verify_token'])

    # Seed full_name from the claimed ClubSpot profile where there is one, so
    # existing users do not land on an empty account page.
    op.execute("""
        UPDATE users
           SET full_name = (SELECT s.name FROM sailors s WHERE s.id = users.sailor_id)
         WHERE sailor_id IS NOT NULL
           AND full_name IS NULL
    """)


def downgrade():
    op.drop_index('ix_users_email_verify_token', table_name='users')
    for name in reversed(FLAG_COLUMNS):
        op.drop_column('users', name)
    for name, _, _ in reversed(IDENTITY_COLUMNS):
        op.drop_column('users', name)
