"""Added error column

Revision ID: 47b937f52d2b
Revises: 0296d30b2db8
Create Date: 2020-12-02 14:37:47.902878

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '47b937f52d2b'
down_revision = '0296d30b2db8'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('baricadr_task', sa.Column('error', sa.Text(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('baricadr_task', 'error')
    # ### end Alembic commands ###
