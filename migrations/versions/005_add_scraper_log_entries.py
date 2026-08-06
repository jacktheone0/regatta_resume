"""Add scraper_log_entries table

Revision ID: 005_add_scraper_log_entries
Revises: 004_add_schools_table
Create Date: 2026-08-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005_add_scraper_log_entries'
down_revision = '004_add_schools_table'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('scraper_log_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('level', sa.String(length=10), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('step', sa.String(length=20), nullable=True),
        sa.Column('section', sa.String(length=20), nullable=True),
        sa.Column('season', sa.String(length=10), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_index(op.f('ix_scraper_log_entries_created_at'),
                    'scraper_log_entries', ['created_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_scraper_log_entries_created_at'),
                  table_name='scraper_log_entries')
    op.drop_table('scraper_log_entries')
