"""Baseline the schema that predates Alembic.

The SQLite-to-PostgreSQL importer creates the current model schema and stamps
this revision. Subsequent schema changes must be represented by new revisions.
"""

from typing import Sequence, Union


revision: str = "20260811_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
