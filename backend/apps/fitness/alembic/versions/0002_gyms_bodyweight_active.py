"""gym keys, variant key/active, 'full' session kind, body_measurements

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24 22:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import apps.fitness.db.types
from apps import fitness as app  # package `app` moved to `apps.fitness`


revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_KINDS = ('upper', 'lower', 'push', 'pull', 'legs', 'calisthenics', 'easy_run', 'intervals',
              'long_run', 'rest', 'other')
_NEW_KINDS = _OLD_KINDS[:2] + ('full',) + _OLD_KINDS[2:]


def _kind_enum(values):
    return sa.Enum(*values, name='session_kind', native_enum=False, create_constraint=True,
                   length=24)


def _fk_off() -> None:
    # SQLite cannot alter a CHECK constraint in place: batch mode recreates the table, and
    # DROP TABLE would trip over child rows. The pragma is a no-op inside a transaction, which is
    # fine: pysqlite only opens one at the first DML statement, after this point.
    if op.get_bind().dialect.name == 'sqlite':
        op.execute('PRAGMA foreign_keys=OFF')


def _fk_on() -> None:
    if op.get_bind().dialect.name == 'sqlite':
        op.execute('PRAGMA foreign_keys=ON')


def upgrade() -> None:
    _fk_off()
    # plain ADD COLUMN + unique index (no table recreate)
    with op.batch_alter_table('gyms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('key', sa.String(length=40), nullable=True))
    op.create_index(op.f('ix_gyms_key'), 'gyms', ['key'], unique=True)

    with op.batch_alter_table('exercise_variants', schema=None) as batch_op:
        batch_op.add_column(sa.Column('key', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('active', sa.Boolean(), server_default=sa.true(),
                                      nullable=False))
    op.create_index(op.f('ix_exercise_variants_key'), 'exercise_variants', ['key'], unique=True)

    # new session kind 'full': rebuild the CHECK constraint (SQLite: batch table recreate)
    with op.batch_alter_table('sessions', schema=None, recreate='always') as batch_op:
        batch_op.alter_column('session_kind', existing_type=_kind_enum(_OLD_KINDS),
                              type_=_kind_enum(_NEW_KINDS), existing_nullable=False)
    with op.batch_alter_table('planned_sessions', schema=None, recreate='always') as batch_op:
        batch_op.alter_column('kind', existing_type=_kind_enum(_OLD_KINDS),
                              type_=_kind_enum(_NEW_KINDS), existing_nullable=False)

    op.create_table('body_measurements',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('measured_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('weight_kg', sa.Float(), nullable=False),
    sa.Column('source', sa.Enum('manual', 'garmin', 'healthkit', name='measurement_source',
              native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('deleted_at', app.db.types.UTCDateTime(), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_body_measurements'))
    )
    with op.batch_alter_table('body_measurements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_body_measurements_measured_at'), ['measured_at'],
                              unique=False)
        batch_op.create_index(batch_op.f('ix_body_measurements_day'), ['day'], unique=False)
    _fk_on()


def downgrade() -> None:
    _fk_off()
    with op.batch_alter_table('body_measurements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_body_measurements_day'))
        batch_op.drop_index(batch_op.f('ix_body_measurements_measured_at'))
    op.drop_table('body_measurements')
    with op.batch_alter_table('planned_sessions', schema=None, recreate='always') as batch_op:
        batch_op.alter_column('kind', existing_type=_kind_enum(_NEW_KINDS),
                              type_=_kind_enum(_OLD_KINDS), existing_nullable=False)
    with op.batch_alter_table('sessions', schema=None, recreate='always') as batch_op:
        batch_op.alter_column('session_kind', existing_type=_kind_enum(_NEW_KINDS),
                              type_=_kind_enum(_OLD_KINDS), existing_nullable=False)
    op.drop_index(op.f('ix_exercise_variants_key'), table_name='exercise_variants')
    with op.batch_alter_table('exercise_variants', schema=None) as batch_op:
        batch_op.drop_column('active')
        batch_op.drop_column('key')
    op.drop_index(op.f('ix_gyms_key'), table_name='gyms')
    with op.batch_alter_table('gyms', schema=None) as batch_op:
        batch_op.drop_column('key')
    _fk_on()
