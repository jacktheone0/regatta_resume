"""Add raw_row_data column to results table

Revision ID: 002_add_raw_data
Revises: 001_initial
Create Date: 2025-01-06

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '002_add_raw_data'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade():
    # Add raw_row_data column to results table
    op.add_column('results', sa.Column('raw_row_data', sa.Text(), nullable=True))


def downgrade():
    # Remove raw_row_data column
    op.drop_column('results', 'raw_row_data')
