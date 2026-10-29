"""Results: crew rows, scoring codes, fleet size; regatta status

Revision ID: 008_results_crew_fleet_status
Revises: 007_regatta_catalog
Create Date: 2026-10-29

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '008_results_crew_fleet_status'
down_revision = '007_regatta_catalog'
branch_labels = None
depends_on = None


def upgrade():
    # Batch mode: plain ALTER on Postgres, copy-and-move on SQLite
    with op.batch_alter_table('results') as batch:
        # A DNF/DNS/DSQ has a scoring code and no place
        batch.alter_column('placement', existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column('scoring_code', sa.String(length=10), nullable=True))
        batch.add_column(sa.Column('fleet_size', sa.Integer(), nullable=True))
        batch.create_index('idx_result_identity', ['sailor_id', 'regatta_id', 'division', 'role'],
                           unique=False)

    with op.batch_alter_table('regattas') as batch:
        batch.add_column(sa.Column('status', sa.String(length=20), nullable=True,
                                   server_default='final'))
        batch.add_column(sa.Column('results_as_of', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('regattas') as batch:
        batch.drop_column('results_as_of')
        batch.drop_column('status')

    # Rows that only have a scoring code cannot satisfy NOT NULL again
    op.execute("DELETE FROM results WHERE placement IS NULL")
    with op.batch_alter_table('results') as batch:
        batch.drop_index('idx_result_identity')
        batch.drop_column('fleet_size')
        batch.drop_column('scoring_code')
        batch.alter_column('placement', existing_type=sa.Integer(), nullable=False)
