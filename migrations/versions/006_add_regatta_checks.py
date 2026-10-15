"""Add ClubSpot regatta checks and run counters

Revision ID: 006_add_regatta_checks
Revises: 005_add_scraper_log_entries
Create Date: 2026-10-15

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '006_add_regatta_checks'
down_revision = '005_add_scraper_log_entries'
branch_labels = None
depends_on = None

RUN_COUNTER_COLUMNS = ('regattas_listed', 'regattas_skipped', 'regattas_remaining',
                       'regattas_empty', 'duration_seconds')


def upgrade():
    op.create_table('clubspot_regatta_checks',
        sa.Column('external_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
        sa.Column('result_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('check_count', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('external_id')
    )

    for column in RUN_COUNTER_COLUMNS:
        op.add_column('scraper_logs', sa.Column(column, sa.Integer(), nullable=True))


def downgrade():
    for column in reversed(RUN_COUNTER_COLUMNS):
        op.drop_column('scraper_logs', column)
    op.drop_table('clubspot_regatta_checks')
