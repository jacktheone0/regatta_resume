"""Catalogue bookkeeping on regatta checks; scraper_state table

Revision ID: 007_regatta_catalog
Revises: 006_add_regatta_checks
Create Date: 2026-10-24

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '007_regatta_catalog'
down_revision = '006_add_regatta_checks'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('clubspot_regatta_checks', sa.Column('first_seen_at', sa.DateTime(), nullable=True))
    op.add_column('clubspot_regatta_checks', sa.Column('last_seen_at', sa.DateTime(), nullable=True))
    op.add_column('clubspot_regatta_checks', sa.Column('source', sa.String(length=20), nullable=True))
    op.add_column('clubspot_regatta_checks', sa.Column('remote_created_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_clubspot_regatta_checks_start_date'),
                    'clubspot_regatta_checks', ['start_date'], unique=False)
    op.create_index(op.f('ix_clubspot_regatta_checks_last_checked_at'),
                    'clubspot_regatta_checks', ['last_checked_at'], unique=False)

    op.create_table('scraper_state',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('key')
    )


def downgrade():
    op.drop_table('scraper_state')
    op.drop_index(op.f('ix_clubspot_regatta_checks_last_checked_at'), table_name='clubspot_regatta_checks')
    op.drop_index(op.f('ix_clubspot_regatta_checks_start_date'), table_name='clubspot_regatta_checks')
    op.drop_column('clubspot_regatta_checks', 'remote_created_at')
    op.drop_column('clubspot_regatta_checks', 'source')
    op.drop_column('clubspot_regatta_checks', 'last_seen_at')
    op.drop_column('clubspot_regatta_checks', 'first_seen_at')
