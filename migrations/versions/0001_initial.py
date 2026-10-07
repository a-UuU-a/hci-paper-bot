"""Paper metadata and recommendation history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "papers",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "authors",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("abstract", sa.Text()),
        sa.Column("venue", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("doi", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("citation_count", sa.Integer()),
        sa.Column(
            "topics",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("source", sa.Text(), nullable=False, server_default="dblp"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("year between 1900 and 2200", name="papers_year_check"),
        sa.CheckConstraint("citation_count >= 0", name="papers_citation_count_check"),
    )
    op.create_index("papers_venue_year_idx", "papers", ["venue", "year"])
    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("paper_id", sa.Text(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status in ('sent', 'failed')", name="recommendations_status_check"),
        sa.CheckConstraint(
            "(status = 'sent' and sent_at is not null) or (status = 'failed' and sent_at is null)",
            name="recommendations_sent_at_check",
        ),
    )
    op.create_index(
        "recommendations_sent_idx",
        "recommendations",
        ["paper_id"],
        postgresql_where=sa.text("status = 'sent'"),
        sqlite_where=sa.text("status = 'sent'"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE public.papers ADD CONSTRAINT papers_authors_array "
            "CHECK (jsonb_typeof(authors) = 'array')"
        )
        op.execute(
            "ALTER TABLE public.papers ADD CONSTRAINT papers_topics_array "
            "CHECK (jsonb_typeof(topics) = 'array')"
        )
        op.execute(
            "ALTER TABLE public.recommendations ALTER COLUMN id SET DEFAULT gen_random_uuid()"
        )
        op.execute("ALTER TABLE public.papers ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE public.recommendations ENABLE ROW LEVEL SECURITY")
        # These roles exist on Supabase, but not on every PostgreSQL installation.
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
                REVOKE ALL ON public.papers, public.recommendations FROM anon;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON public.papers, public.recommendations FROM authenticated;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'service_role') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON public.papers, public.recommendations TO service_role;
            END IF;
        END $$""")
        op.execute("""CREATE OR REPLACE FUNCTION public.set_paper_updated_at()
            RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
            BEGIN new.updated_at = now(); RETURN new; END; $$""")
        op.execute("""CREATE TRIGGER papers_updated_at BEFORE UPDATE ON public.papers
            FOR EACH ROW EXECUTE FUNCTION public.set_paper_updated_at()""")


def downgrade():
    op.drop_table("recommendations")
    op.drop_table("papers")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS public.set_paper_updated_at()")
