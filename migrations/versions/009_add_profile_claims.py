"""Add profile_claims and backfill from users.sailor_id

Revision ID: 009_add_profile_claims
Revises: 008_add_user_identity
Create Date: 2026-10-23

One account can now claim many scraped profiles -- a sailor normally appears as
a ClubSpot `Sailor` row AND one or more HS/College `SailorName` rows, which the
old single `User.sailor_id` FK could not express (and which is why the
HS/College claim path was never wired up).

`users.sailor_id` is deliberately LEFT IN PLACE here so a mid-deploy rollback
still works. Migration 011 drops it, once every reader goes through
ProfileClaim.

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '009_add_profile_claims'
down_revision = '008_add_user_identity'
branch_labels = None
depends_on = None


def _is_pg():
    return op.get_bind().dialect.name == 'postgresql'


def _true():
    """Boolean literal for an INSERT/UPDATE value, per dialect."""
    return 'true' if _is_pg() else '1'


def false_default():
    """
    Server default for a NOT NULL boolean, per dialect.

    Must be sa.text(): a plain string is quoted into `DEFAULT 'false'`, which
    SQLite stores as TEXT and SQLAlchemy's Boolean then reads back as True.
    """
    return sa.text('false' if _is_pg() else '0')


def upgrade():
    op.create_table('profile_claims',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('sailor_id', sa.Integer(), nullable=True),
        sa.Column('sailor_name_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='approved'),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=false_default()),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('requested_at', sa.DateTime(), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('decided_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_claim_user'),
        sa.ForeignKeyConstraint(['sailor_id'], ['sailors.id'], name='fk_claim_sailor'),
        sa.ForeignKeyConstraint(['sailor_name_id'], ['sailor_names.id'], name='fk_claim_sailor_name'),
        sa.ForeignKeyConstraint(['decided_by_user_id'], ['users.id'], name='fk_claim_decided_by'),
        sa.PrimaryKeyConstraint('id'),
        # A profile is claimable exactly once. NULLs are not compared by either
        # Postgres or SQLite, so clubspot claims (sailor_name_id NULL) and name
        # claims (sailor_id NULL) coexist freely.
        sa.UniqueConstraint('sailor_id', name='uq_claim_sailor'),
        sa.UniqueConstraint('sailor_name_id', name='uq_claim_sailor_name'),
        sa.CheckConstraint(
            '(sailor_id IS NULL) != (sailor_name_id IS NULL)',
            name='ck_claim_one_target'
        ),
    )
    op.create_index('ix_profile_claims_user_id', 'profile_claims', ['user_id'])

    # Backfill: every existing User.sailor_id becomes an approved primary claim.
    # GROUP BY sailor_id collapses the (impossible-but-unconstrained) case of
    # two accounts pointing at one sailor; the lowest user id wins, which would
    # otherwise trip uq_claim_sailor and abort the migration.
    op.execute(f"""
        INSERT INTO profile_claims
            (user_id, sailor_id, status, is_primary, requested_at, decided_at)
        SELECT MIN(u.id), u.sailor_id, 'approved', {_true()},
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
          FROM users u
         WHERE u.sailor_id IS NOT NULL
         GROUP BY u.sailor_id
    """)

    # Keep the denormalized flag honest after the backfill.
    op.execute(f"""
        UPDATE sailors SET is_claimed = {_true()}
         WHERE id IN (SELECT sailor_id FROM profile_claims
                       WHERE status = 'approved' AND sailor_id IS NOT NULL)
    """)


def downgrade():
    op.drop_index('ix_profile_claims_user_id', table_name='profile_claims')
    op.drop_table('profile_claims')
