"""Add raw_row_data to results

Revision ID: 002
Revises: 001
Create Date: 2026-01-27

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade():
    # Add raw_row_data column to results table
    op.add_column('results', sa.Column('raw_row_data', sa.Text(), nullable=True))


def downgrade():
    # Remove raw_row_data column
    op.drop_column('results', 'raw_row_data')
