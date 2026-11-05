"""Add manual_results for sailor-entered regattas

Revision ID: 010_add_manual_results
Revises: 009_add_profile_claims
Create Date: 2026-10-23

Results the sailor adds themselves, for regattas the scrapers missed. Owned by
`user_id`, not by a claimed profile: a manual result is a claim about the
person, who may hold several profiles, and it has to survive a profile being
re-scraped.

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '010_add_manual_results'
down_revision = '009_add_profile_claims'
branch_labels = None
depends_on = None


def false_default():
    """
    Server default for a NOT NULL boolean, per dialect.

    Must be sa.text(): a plain string is quoted into `DEFAULT 'false'`, which
    SQLite stores as TEXT and SQLAlchemy's Boolean then reads back as True.
    """
    return sa.text('false' if op.get_bind().dialect.name == 'postgresql' else '0')


def upgrade():
    op.create_table('manual_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('regatta_name', sa.String(length=300), nullable=False),
        sa.Column('regatta_date', sa.Date(), nullable=True),
        sa.Column('location', sa.String(length=200), nullable=True),
        sa.Column('place_numeric', sa.Integer(), nullable=True),
        sa.Column('total_boats', sa.Integer(), nullable=True),
        sa.Column('boat_type', sa.String(length=100), nullable=True),
        sa.Column('division', sa.String(length=50), nullable=True),
        sa.Column('role', sa.String(length=20), nullable=True),
        sa.Column('team_name', sa.String(length=200), nullable=True),
        sa.Column('crew_partner', sa.String(length=200), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default=false_default()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_manual_result_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_manual_results_user_id', 'manual_results', ['user_id'])
    op.create_index('ix_manual_results_regatta_date', 'manual_results', ['regatta_date'])


def downgrade():
    op.drop_index('ix_manual_results_regatta_date', table_name='manual_results')
    op.drop_index('ix_manual_results_user_id', table_name='manual_results')
    op.drop_table('manual_results')
