"""Move resume ownership to the user; qualify selected result ids

Revision ID: 011_resume_link_user_owner
Revises: 010_add_manual_results
Create Date: 2026-10-23

Two changes, both needed before a resume can mix sources:

1. `resume_links.user_id` becomes the owner and `sailor_id` becomes nullable.
   While ownership ran through `sailors`, an HS/College-only sailor could not
   own a resume at all.
2. `selected_result_ids` moves from bare `results.id` integers to qualified
   keys ("clubspot:12", "hs:88", "college:9", "manual:3"). Bare ints could not
   address HS/College rows, whose ids collide across tables.

The rewrite runs in Python rather than SQL: the column is JSON on Postgres and
TEXT on SQLite, and json manipulation is not portable between them.

"""
from alembic import op
import sqlalchemy as sa
import json


# revision identifiers, used by Alembic.
revision = '011_resume_link_user_owner'
down_revision = '010_add_manual_results'
branch_labels = None
depends_on = None


def upgrade():
    # One batch block: SQLite cannot ALTER a constraint or a column's
    # nullability in place, so batch mode recreates the table once for all
    # three changes. On Postgres these are plain ALTERs.
    with op.batch_alter_table('resume_links') as batch:
        batch.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch.alter_column('sailor_id', existing_type=sa.Integer(), nullable=True)
        batch.create_foreign_key('fk_resume_link_user', 'users', ['user_id'], ['id'])

    op.create_index('ix_resume_links_user_id', 'resume_links', ['user_id'])

    # Derive the owner from the claim on the resume's sailor. profile_claims is
    # used rather than users.sailor_id so this still works after 011 drops it;
    # uq_claim_sailor guarantees the subquery returns at most one row.
    op.execute("""
        UPDATE resume_links
           SET user_id = (SELECT pc.user_id FROM profile_claims pc
                           WHERE pc.sailor_id = resume_links.sailor_id
                             AND pc.status = 'approved')
         WHERE sailor_id IS NOT NULL
           AND user_id IS NULL
    """)

    _rewrite_selected_ids(to_qualified=True)


def downgrade():
    _rewrite_selected_ids(to_qualified=False)

    # Resumes owned by a user with no ClubSpot sailor cannot be represented in
    # the old schema; drop them rather than violate the restored NOT NULL.
    op.execute('DELETE FROM resume_links WHERE sailor_id IS NULL')

    op.drop_index('ix_resume_links_user_id', table_name='resume_links')
    with op.batch_alter_table('resume_links') as batch:
        batch.drop_constraint('fk_resume_link_user', type_='foreignkey')
        batch.alter_column('sailor_id', existing_type=sa.Integer(), nullable=False)
        batch.drop_column('user_id')


def _rewrite_selected_ids(to_qualified):
    """Convert selected_result_ids between bare ints and qualified keys."""
    bind = op.get_bind()

    # Postgres stores this as json and will not take a bare text parameter;
    # SQLite stores it as TEXT and has no cast to apply.
    if bind.dialect.name == 'postgresql':
        update_sql = ('UPDATE resume_links SET selected_result_ids = CAST(:v AS json) '
                      'WHERE id = :i')
    else:
        update_sql = 'UPDATE resume_links SET selected_result_ids = :v WHERE id = :i'

    rows = bind.execute(sa.text(
        'SELECT id, selected_result_ids FROM resume_links '
        'WHERE selected_result_ids IS NOT NULL'
    )).fetchall()

    for row_id, raw in rows:
        value = raw
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                continue
        if not isinstance(value, list):
            continue

        if to_qualified:
            converted = [
                f'clubspot:{item}' if not isinstance(item, str) else item
                for item in value
            ]
        else:
            # Only ClubSpot entries survive a downgrade; the old schema has
            # nowhere to put HS/college/manual selections.
            converted = [
                int(str(item).split(':', 1)[1])
                for item in value
                if str(item).startswith('clubspot:')
            ]

        if converted != value:
            bind.execute(sa.text(update_sql),
                         {'v': json.dumps(converted), 'i': row_id})
