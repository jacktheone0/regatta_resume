"""Add schools table

Revision ID: 004_add_schools_table
Revises: 003_add_hs_college_tables
Create Date: 2026-02-03

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004_add_schools_table'
down_revision = '003_add_hs_college_tables'
branch_labels = None
depends_on = None


def upgrade():
    # Create schools table
    op.create_table('schools',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('district', sa.String(length=200), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('url_slug', sa.String(length=200), nullable=False),
        sa.Column('full_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('url_slug', 'source', name='uq_school_slug_source')
    )

    # Create indexes
    op.create_index('idx_school_name_source', 'schools', ['name', 'source'], unique=False)
    op.create_index(op.f('ix_schools_name'), 'schools', ['name'], unique=False)


def downgrade():
    # Drop indexes
    op.drop_index(op.f('ix_schools_name'), table_name='schools')
    op.drop_index('idx_school_name_source', table_name='schools')

    # Drop table
    op.drop_table('schools')
