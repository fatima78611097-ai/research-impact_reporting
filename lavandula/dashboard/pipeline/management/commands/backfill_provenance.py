"""
One-time backfill of org_provenance from existing pipeline tables.

Reads nonprofits_seed, crawled_orgs, and corpus to populate initial provenance state.
Idempotent: uses INSERT ON CONFLICT DO NOTHING for initial rows, then UPDATEs.
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Backfill org_provenance table from existing pipeline data"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show SQL without executing")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        steps = [
            ("seed", self._backfill_seed),
            ("resolve", self._backfill_resolve),
            ("crawl", self._backfill_crawl),
            ("classify", self._backfill_classify),
        ]

        for name, fn in steps:
            self.stdout.write(f"Backfilling {name}...")
            count = fn(dry_run)
            self.stdout.write(f"  {name}: {count} rows affected")

        self.stdout.write(self.style.SUCCESS("Backfill complete."))

    def _backfill_seed(self, dry_run: bool) -> int:
        sql = """
        INSERT INTO lava_pipeline.org_provenance (ein, seed_status, seed_completed_at, updated_at)
        SELECT ein, 'completed', COALESCE(resolver_updated_at, NOW()), NOW()
        FROM lava_corpus.nonprofits_seed
        ON CONFLICT (ein) DO NOTHING
        """
        return self._execute(sql, dry_run)

    def _backfill_resolve(self, dry_run: bool) -> int:
        sql = """
        UPDATE lava_pipeline.org_provenance p SET
            resolve_status = CASE
                WHEN s.resolver_status IN ('resolved', 'accepted') THEN 'completed'
                WHEN s.resolver_status IN ('rejected', 'error') THEN 'failed'
                WHEN s.resolver_status IS NOT NULL THEN 'in_progress'
                ELSE 'not_started'
            END,
            resolve_completed_at = CASE
                WHEN s.resolver_status IN ('resolved', 'accepted') THEN s.resolver_updated_at
                ELSE NULL
            END,
            updated_at = NOW()
        FROM lava_corpus.nonprofits_seed s
        WHERE p.ein = s.ein
        """
        return self._execute(sql, dry_run)

    def _backfill_crawl(self, dry_run: bool) -> int:
        sql = """
        UPDATE lava_pipeline.org_provenance p SET
            crawl_status = CASE
                WHEN co.ein IS NOT NULL THEN 'completed'
                ELSE 'not_started'
            END,
            crawl_completed_at = CASE
                WHEN co.ein IS NOT NULL THEN co.last_crawled_at::timestamptz
                ELSE NULL
            END,
            updated_at = NOW()
        FROM lava_pipeline.org_provenance p2
        LEFT JOIN lava_corpus.crawled_orgs co ON p2.ein = co.ein
        WHERE p.ein = p2.ein
        """
        return self._execute(sql, dry_run)

    def _backfill_classify(self, dry_run: bool) -> int:
        sql = """
        UPDATE lava_pipeline.org_provenance p SET
            classify_status = CASE
                WHEN NOT EXISTS (
                    SELECT 1 FROM lava_corpus.corpus c WHERE c.source_org_ein = p.ein
                ) THEN 'not_applicable'
                WHEN NOT EXISTS (
                    SELECT 1 FROM lava_corpus.corpus c
                    WHERE c.source_org_ein = p.ein AND c.material_type IS NULL
                ) THEN 'completed'
                ELSE 'in_progress'
            END,
            updated_at = NOW()
        """
        return self._execute(sql, dry_run)

    def _execute(self, sql: str, dry_run: bool) -> int:
        if dry_run:
            self.stdout.write(f"  [DRY RUN] {sql.strip()[:100]}...")
            return 0
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.rowcount or 0
