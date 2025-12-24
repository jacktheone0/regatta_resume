"""Initial migration - create all tables

Revision ID: 001_initial
Revises:
Create Date: 2024-12-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create users table
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.Column('sailor_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # Create sailors table
    op.create_table('sailors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('name_normalized', sa.String(length=200), nullable=True),
        sa.Column('home_club', sa.String(length=200), nullable=True),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('profile_image_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_claimed', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sailors_name'), 'sailors', ['name'], unique=False)
    op.create_index(op.f('ix_sailors_name_normalized'), 'sailors', ['name_normalized'], unique=False)

    # Create regattas table
    op.create_table('regattas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('location', sa.String(length=200), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('fleet_type', sa.String(length=100), nullable=True),
        sa.Column('external_id', sa.String(length=200), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id')
    )
    op.create_index(op.f('ix_regattas_start_date'), 'regattas', ['start_date'], unique=False)

    # Create scraper_logs table
    op.create_table('scraper_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('regattas_scraped', sa.Integer(), nullable=True),
        sa.Column('sailors_added', sa.Integer(), nullable=True),
        sa.Column('results_added', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create results table
    op.create_table('results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sailor_id', sa.Integer(), nullable=False),
        sa.Column('regatta_id', sa.Integer(), nullable=False),
        sa.Column('placement', sa.Integer(), nullable=False),
        sa.Column('boat_type', sa.String(length=100), nullable=True),
        sa.Column('role', sa.String(length=20), nullable=True),
        sa.Column('points_scored', sa.Float(), nullable=True),
        sa.Column('division', sa.String(length=50), nullable=True),
        sa.Column('team_name', sa.String(length=200), nullable=True),
        sa.Column('crew_partner', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['regatta_id'], ['regattas.id'], ),
        sa.ForeignKeyConstraint(['sailor_id'], ['sailors.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_results_sailor_id'), 'results', ['sailor_id'], unique=False)
    op.create_index(op.f('ix_results_regatta_id'), 'results', ['regatta_id'], unique=False)
    op.create_index('idx_sailor_regatta', 'results', ['sailor_id', 'regatta_id'], unique=False)

    # Create resume_links table
    op.create_table('resume_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sailor_id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('custom_bio', sa.Text(), nullable=True),
        sa.Column('selected_result_ids', sa.JSON(), nullable=True),
        sa.Column('template_style', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_accessed', sa.DateTime(), nullable=True),
        sa.Column('access_count', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['sailor_id'], ['sailors.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token')
    )
    op.create_index(op.f('ix_resume_links_token'), 'resume_links', ['token'], unique=True)

    # Add foreign key for users.sailor_id
    op.create_foreign_key(None, 'users', 'sailors', ['sailor_id'], ['id'])


def downgrade():
    op.drop_table('resume_links')
    op.drop_table('results')
    op.drop_table('scraper_logs')
    op.drop_table('regattas')
    op.drop_table('sailors')
    op.drop_table('users')
