"""Initial MedRAG schema.

On SQLite the app uses create_all(); this revision is the PostgreSQL path.

Revision ID: 0001
Revises: 
Create Date: 2026-11-08
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tables are created from SQLAlchemy metadata to stay in sync with models.
    from app.database import Base
    from app import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    # alembic.runtime has no importable `context` module; the MigrationContext
    # is reached through op.get_context().
    if op.get_context().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    from app.database import Base
    from app import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
