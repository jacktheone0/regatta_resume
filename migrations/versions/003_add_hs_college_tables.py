"""Add HS/College tables and sailor names

Revision ID: 003
Revises: 002
Create Date: 2026-01-27

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade():
    # Create sailor_names table
    op.create_table('sailor_names',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('name_normalized', sa.String(length=200), nullable=False),
        sa.Column('is_claimed', sa.Boolean(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
        sa.Column('school', sa.String(length=200), nullable=True),
        sa.Column('graduation_year', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sailor_names_name'), 'sailor_names', ['name'], unique=False)
    op.create_index(op.f('ix_sailor_names_name_normalized'), 'sailor_names', ['name_normalized'], unique=True)

    # Create hs_results table
    op.create_table('hs_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sailor_name_id', sa.Integer(), nullable=False),
        sa.Column('regatta_name', sa.String(length=300), nullable=False),
        sa.Column('regatta_link', sa.String(length=500), nullable=True),
        sa.Column('regatta_date', sa.Date(), nullable=True),
        sa.Column('place', sa.String(length=50), nullable=True),
        sa.Column('place_numeric', sa.Integer(), nullable=True),
        sa.Column('total_boats', sa.Integer(), nullable=True),
        sa.Column('position', sa.String(length=20), nullable=True),
        sa.Column('division', sa.String(length=50), nullable=True),
        sa.Column('school', sa.String(length=200), nullable=True),
        sa.Column('raw_row_data', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['sailor_name_id'], ['sailor_names.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_hs_sailor_regatta', 'hs_results', ['sailor_name_id', 'regatta_name'], unique=False)
    op.create_index(op.f('ix_hs_results_regatta_date'), 'hs_results', ['regatta_date'], unique=False)
    op.create_index(op.f('ix_hs_results_regatta_name'), 'hs_results', ['regatta_name'], unique=False)
    op.create_index(op.f('ix_hs_results_sailor_name_id'), 'hs_results', ['sailor_name_id'], unique=False)

    # Create college_results table
    op.create_table('college_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sailor_name_id', sa.Integer(), nullable=False),
        sa.Column('regatta_name', sa.String(length=300), nullable=False),
        sa.Column('regatta_link', sa.String(length=500), nullable=True),
        sa.Column('regatta_date', sa.Date(), nullable=True),
        sa.Column('place', sa.String(length=50), nullable=True),
        sa.Column('place_numeric', sa.Integer(), nullable=True),
        sa.Column('total_boats', sa.Integer(), nullable=True),
        sa.Column('position', sa.String(length=20), nullable=True),
        sa.Column('division', sa.String(length=50), nullable=True),
        sa.Column('school', sa.String(length=200), nullable=True),
        sa.Column('raw_row_data', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['sailor_name_id'], ['sailor_names.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_college_sailor_regatta', 'college_results', ['sailor_name_id', 'regatta_name'], unique=False)
    op.create_index(op.f('ix_college_results_regatta_date'), 'college_results', ['regatta_date'], unique=False)
    op.create_index(op.f('ix_college_results_regatta_name'), 'college_results', ['regatta_name'], unique=False)
    op.create_index(op.f('ix_college_results_sailor_name_id'), 'college_results', ['sailor_name_id'], unique=False)


def downgrade():
    # Drop tables in reverse order
    op.drop_index(op.f('ix_college_results_sailor_name_id'), table_name='college_results')
    op.drop_index(op.f('ix_college_results_regatta_name'), table_name='college_results')
    op.drop_index(op.f('ix_college_results_regatta_date'), table_name='college_results')
    op.drop_index('idx_college_sailor_regatta', table_name='college_results')
    op.drop_table('college_results')

    op.drop_index(op.f('ix_hs_results_sailor_name_id'), table_name='hs_results')
    op.drop_index(op.f('ix_hs_results_regatta_name'), table_name='hs_results')
    op.drop_index(op.f('ix_hs_results_regatta_date'), table_name='hs_results')
    op.drop_index('idx_hs_sailor_regatta', table_name='hs_results')
    op.drop_table('hs_results')

    op.drop_index(op.f('ix_sailor_names_name_normalized'), table_name='sailor_names')
    op.drop_index(op.f('ix_sailor_names_name'), table_name='sailor_names')
    op.drop_table('sailor_names')
