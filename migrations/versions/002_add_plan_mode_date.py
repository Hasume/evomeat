"""Add plan_mode_date to users

Revision ID: 002_add_plan_mode_date
Revises: 001_add_nutrition_tracking
Create Date: 2026-09-08

"""
from alembic import op


revision = '002_add_plan_mode_date'
down_revision = '001_add_nutrition_tracking'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS — колонку мог уже добавить init_db при старте бота.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_mode_date DATE")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS plan_mode_date")
