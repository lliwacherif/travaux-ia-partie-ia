"""add chatbot_daily_metrics and chatbot_conversations tables

Revision ID: 25c5c6de45ea
Revises: 5d8a93629525
Create Date: 2026-06-25 11:01:27.119864

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '25c5c6de45ea'
down_revision: Union[str, Sequence[str], None] = '5d8a93629525'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('chatbot_conversations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('chatbot_source', sa.String(length=32), nullable=False),
    sa.Column('user_message', sa.Text(), nullable=False),
    sa.Column('ai_response', sa.Text(), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('completion_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('total_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_fallback', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chatbot_conversations_chatbot_source'), 'chatbot_conversations', ['chatbot_source'], unique=False)
    op.create_index(op.f('ix_chatbot_conversations_created_at'), 'chatbot_conversations', ['created_at'], unique=False)
    op.create_table('chatbot_daily_metrics',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('chatbot_source', sa.String(length=32), nullable=False),
    sa.Column('total_conversations', sa.Integer(), server_default='0', nullable=False),
    sa.Column('total_messages', sa.Integer(), server_default='0', nullable=False),
    sa.Column('total_prompt_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('total_completion_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('total_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('total_errors', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('date', 'chatbot_source', name='uq_metrics_date_source')
    )
    op.create_index(op.f('ix_chatbot_daily_metrics_chatbot_source'), 'chatbot_daily_metrics', ['chatbot_source'], unique=False)
    op.create_index(op.f('ix_chatbot_daily_metrics_date'), 'chatbot_daily_metrics', ['date'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_chatbot_daily_metrics_date'), table_name='chatbot_daily_metrics')
    op.drop_index(op.f('ix_chatbot_daily_metrics_chatbot_source'), table_name='chatbot_daily_metrics')
    op.drop_table('chatbot_daily_metrics')
    op.drop_index(op.f('ix_chatbot_conversations_created_at'), table_name='chatbot_conversations')
    op.drop_index(op.f('ix_chatbot_conversations_chatbot_source'), table_name='chatbot_conversations')
    op.drop_table('chatbot_conversations')
