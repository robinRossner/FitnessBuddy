"""advisor step 6: consultation status/usage/cost, per-change table, outcome verdicts

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25 13:32:13.501331

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db.types


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('advisor_changes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('consultation_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('param', sa.String(length=160), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('old_value', sa.JSON(), nullable=False),
    sa.Column('new_value', sa.JSON(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('risk', sa.String(length=8), nullable=False),
    sa.Column('claude_risk', sa.String(length=8), nullable=True),
    sa.Column('valid', sa.Boolean(), nullable=False),
    sa.Column('validation_error', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('applied_config_version_id', sa.Integer(), nullable=True),
    sa.Column('applied_at', app.db.types.UTCDateTime(), nullable=True),
    sa.ForeignKeyConstraint(['applied_config_version_id'], ['config_versions.id'], name=op.f('fk_advisor_changes_applied_config_version_id_config_versions')),
    sa.ForeignKeyConstraint(['consultation_id'], ['advisor_consultations.id'], name=op.f('fk_advisor_changes_consultation_id_advisor_consultations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_advisor_changes'))
    )
    with op.batch_alter_table('advisor_changes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_advisor_changes_consultation_id'), ['consultation_id'], unique=False)

    with op.batch_alter_table('advisor_consultations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('status', sa.String(length=24), server_default='queued', nullable=False))
        batch_op.add_column(sa.Column('kind', sa.String(length=12), server_default='trigger', nullable=False))
        batch_op.add_column(sa.Column('trigger_info', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('question', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('usage', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('cost_usd', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('dry_run', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('reject_note', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('week_start', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('applied_config_version_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_advisor_consultations_status'), ['status'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_advisor_consultations_applied_config_version_id_config_versions'), 'config_versions', ['applied_config_version_id'], ['id'])

    with op.batch_alter_table('outcomes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column('verdict', sa.String(length=12), nullable=True))
        batch_op.add_column(sa.Column('consultation_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('config_version_id', sa.Integer(), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('uq_outcomes_config_version_id'), ['config_version_id'])
        batch_op.create_foreign_key(batch_op.f('fk_outcomes_consultation_id_advisor_consultations'), 'advisor_consultations', ['consultation_id'], ['id'])
        batch_op.create_foreign_key(batch_op.f('fk_outcomes_config_version_id_config_versions'), 'config_versions', ['config_version_id'], ['id'])



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('outcomes', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_outcomes_config_version_id_config_versions'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('fk_outcomes_consultation_id_advisor_consultations'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_outcomes_config_version_id'), type_='unique')
        batch_op.drop_column('config_version_id')
        batch_op.drop_column('consultation_id')
        batch_op.drop_column('verdict')
        batch_op.drop_column('kind')

    with op.batch_alter_table('advisor_consultations', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_advisor_consultations_applied_config_version_id_config_versions'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_advisor_consultations_status'))
        batch_op.drop_column('applied_config_version_id')
        batch_op.drop_column('week_start')
        batch_op.drop_column('reject_note')
        batch_op.drop_column('dry_run')
        batch_op.drop_column('error')
        batch_op.drop_column('cost_usd')
        batch_op.drop_column('usage')
        batch_op.drop_column('question')
        batch_op.drop_column('trigger_info')
        batch_op.drop_column('kind')
        batch_op.drop_column('status')
        batch_op.drop_column('updated_at')

    with op.batch_alter_table('advisor_changes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_advisor_changes_consultation_id'))

    op.drop_table('advisor_changes')
