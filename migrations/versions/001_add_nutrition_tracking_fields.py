"""Add data_source to diary_entries and reminder fields to users

Revision ID: 001_add_nutrition_tracking
Revises: 
Create Date: 2026-01-09

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001_add_nutrition_tracking'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем enum для источника данных
    datasource_enum = sa.Enum('manual', 'database', 'ai_estimate', name='datasource')
    datasource_enum.create(op.get_bind(), checkfirst=True)
    
    # Добавляем поле data_source в diary_entries
    op.add_column(
        'diary_entries',
        sa.Column(
            'data_source', 
            sa.Enum('manual', 'database', 'ai_estimate', name='datasource'),
            nullable=True,
            server_default='ai_estimate'
        )
    )
    
    # Обновляем существующие записи
    op.execute("UPDATE diary_entries SET data_source = 'ai_estimate' WHERE data_source IS NULL")
    
    # Делаем поле NOT NULL
    op.alter_column('diary_entries', 'data_source', nullable=False)
    
    # Добавляем поля для напоминаний в users
    op.add_column(
        'users',
        sa.Column('reminders_enabled', sa.Boolean(), nullable=True, server_default='true')
    )
    op.add_column(
        'users',
        sa.Column('max_reminders_per_day', sa.Integer(), nullable=True, server_default='2')
    )
    op.add_column(
        'users',
        sa.Column('last_reminder_date', sa.Date(), nullable=True)
    )
    op.add_column(
        'users',
        sa.Column('reminders_sent_today', sa.Integer(), nullable=True, server_default='0')
    )
    op.add_column(
        'users',
        sa.Column('last_reminder_type', sa.String(), nullable=True)
    )
    op.add_column(
        'users',
        sa.Column('timezone', sa.String(), nullable=True, server_default="'Europe/Moscow'")
    )
    
    # Обновляем существующих пользователей
    op.execute("UPDATE users SET reminders_enabled = true WHERE reminders_enabled IS NULL")
    op.execute("UPDATE users SET max_reminders_per_day = 2 WHERE max_reminders_per_day IS NULL")
    op.execute("UPDATE users SET reminders_sent_today = 0 WHERE reminders_sent_today IS NULL")
    op.execute("UPDATE users SET timezone = 'Europe/Moscow' WHERE timezone IS NULL")


def downgrade() -> None:
    # Удаляем поля напоминаний из users
    op.drop_column('users', 'timezone')
    op.drop_column('users', 'last_reminder_type')
    op.drop_column('users', 'reminders_sent_today')
    op.drop_column('users', 'last_reminder_date')
    op.drop_column('users', 'max_reminders_per_day')
    op.drop_column('users', 'reminders_enabled')
    
    # Удаляем поле data_source из diary_entries
    op.drop_column('diary_entries', 'data_source')
    
    # Удаляем enum
    datasource_enum = sa.Enum('manual', 'database', 'ai_estimate', name='datasource')
    datasource_enum.drop(op.get_bind(), checkfirst=True)

