from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app import models  # noqa: F401
from app.config import get_settings
from app.db import Base

BASE_REVISION = "0001_initial"
STRAVA_REVISION = "0002_strava_integration"
LINKS_REVISION = "0003_activity_source_links"
HEAD_REVISION = "0006_ai_responses_runtime"

Strategy = Literal[
    "current",
    "stamp_head",
    "stamp_0003_upgrade",
    "stamp_0002_upgrade",
    "stamp_0001_upgrade",
    "divergent",
]


@dataclass(frozen=True)
class AdoptionReport:
    database_path: Path
    alembic_version: str | None
    strategy: Strategy
    tables: list[str]
    missing_tables: list[str]
    table_differences: dict[str, list[str]]
    counts: dict[str, int]
    fit_reference_samples: int

    @property
    def is_safe(self) -> bool:
        return self.strategy != "divergent"


def inspect_database(database_path: Path) -> AdoptionReport:
    database_path = database_path.resolve()
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())
    alembic_version = _read_alembic_version(database_path)
    missing_tables = [name for name in Base.metadata.tables if name not in tables]
    table_differences = _table_differences(inspector, tables)
    counts = _counts(database_path, tables)
    fit_samples = _fit_reference_samples(database_path, tables)
    strategy = _choose_strategy(tables, alembic_version, missing_tables, table_differences)
    engine.dispose()
    return AdoptionReport(
        database_path=database_path,
        alembic_version=alembic_version,
        strategy=strategy,
        tables=tables,
        missing_tables=missing_tables,
        table_differences=table_differences,
        counts=counts,
        fit_reference_samples=fit_samples,
    )


def adopt_database(database_path: Path, *, confirm: bool, backup_dir: Path | None = None) -> AdoptionReport:
    if not confirm:
        raise RuntimeError("Use confirm=True ou --yes para aplicar a adocao Alembic.")

    before = inspect_database(database_path)
    if not before.is_safe:
        raise RuntimeError(f"Schema divergente: {before.table_differences}")

    _sqlite_integrity_check(before.database_path)
    _create_backup(before.database_path, backup_dir)
    _apply_strategy(before)
    after = inspect_database(before.database_path)
    if not after.is_safe:
        raise RuntimeError(f"Schema divergente apos adocao: {after.table_differences}")
    for table, before_count in before.counts.items():
        if after.counts.get(table) != before_count:
            raise RuntimeError("Contagens mudaram durante a adocao Alembic.")
    if after.fit_reference_samples != before.fit_reference_samples:
        raise RuntimeError("Contagens mudaram durante a adocao Alembic.")
    return after


def _read_alembic_version(database_path: Path) -> str | None:
    with sqlite3.connect(database_path) as con:
        tables = {
            row[0]
            for row in con.execute(
                "select name from sqlite_master where type=char(116,97,98,108,101)"
            )
        }
        if "alembic_version" not in tables:
            return None
        rows = [row[0] for row in con.execute("select version_num from alembic_version")]
    return rows[0] if rows else None


def _table_differences(inspector, tables: list[str]) -> dict[str, list[str]]:
    differences: dict[str, list[str]] = {}
    for table_name, table in Base.metadata.tables.items():
        if table_name not in tables:
            continue
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        expected_columns = set(table.columns.keys())
        missing_columns = sorted(expected_columns - actual_columns)
        if missing_columns:
            differences.setdefault(table_name, []).append(
                "missing columns: " + ", ".join(missing_columns)
            )

    if "integration_states" in tables:
        _require_indexes(
            inspector,
            differences,
            "integration_states",
            {"ix_integration_states_user_id", "ix_integration_states_data_source_id"},
        )
        _require_unique(inspector, differences, "integration_states", {"user_id", "data_source_id"})
    if "activity_source_links" in tables:
        _require_unique(inspector, differences, "activity_source_links", {"data_source_id", "external_id"})
    if "external_records" in tables:
        _require_unique(
            inspector,
            differences,
            "external_records",
            {"data_source_id", "kind", "external_id"},
        )
    return differences


def _require_indexes(inspector, differences: dict[str, list[str]], table: str, expected: set[str]) -> None:
    existing = {index["name"] for index in inspector.get_indexes(table)}
    missing = sorted(expected - existing)
    if missing:
        differences.setdefault(table, []).append("missing indexes: " + ", ".join(missing))


def _require_unique(inspector, differences: dict[str, list[str]], table: str, columns: set[str]) -> None:
    uniques = [set(item["column_names"]) for item in inspector.get_unique_constraints(table)]
    if columns not in uniques:
        differences.setdefault(table, []).append("missing unique constraint: " + ", ".join(sorted(columns)))


def _counts(database_path: Path, tables: list[str]) -> dict[str, int]:
    tracked = [
        "activities",
        "activity_samples",
        "activity_source_links",
        "external_records",
        "integration_states",
        "sync_logs",
    ]
    with sqlite3.connect(database_path) as con:
        return {
            table: con.execute(f"select count(*) from {table}").fetchone()[0]
            for table in tracked
            if table in tables
        }


def _fit_reference_samples(database_path: Path, tables: list[str]) -> int:
    if "activities" not in tables or "activity_samples" not in tables:
        return 0
    with sqlite3.connect(database_path) as con:
        return con.execute(
            """
            select count(s.id)
            from activities a
            join activity_samples s on s.activity_id = a.id
            where a.distance_meters between 4000 and 4010
              and a.total_duration_seconds between 1560 and 1580
            """
        ).fetchone()[0]


def _choose_strategy(
    tables: list[str],
    alembic_version: str | None,
    missing_tables: list[str],
    table_differences: dict[str, list[str]],
) -> Strategy:
    if table_differences:
        return "divergent"
    if alembic_version == HEAD_REVISION:
        return "current"
    if alembic_version:
        return "current"

    base_missing = [
        name
        for name in missing_tables
        if name not in {"integration_states", "activity_source_links", "external_records"}
    ]
    if base_missing:
        return "divergent"
    has_integration = "integration_states" in tables
    has_links = "activity_source_links" in tables
    has_external_records = "external_records" in tables
    if has_integration and has_links and has_external_records:
        return "stamp_head"
    if has_integration and has_links:
        return "stamp_0003_upgrade"
    if has_integration:
        return "stamp_0002_upgrade"
    return "stamp_0001_upgrade"


def _sqlite_integrity_check(database_path: Path) -> None:
    with sqlite3.connect(database_path) as con:
        result = con.execute("pragma integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite integrity_check falhou: {result}")


def _create_backup(database_path: Path, backup_dir: Path | None) -> Path:
    target_dir = backup_dir.resolve() if backup_dir else database_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = target_dir / f"{database_path.stem}.backup-{stamp}{database_path.suffix}"
    shutil.copy2(database_path, backup_path)
    return backup_path


def _apply_strategy(report: AdoptionReport) -> None:
    os.environ["DATABASE_URL"] = f"sqlite:///{report.database_path.as_posix()}"
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    if report.strategy == "current":
        command.upgrade(cfg, "head")
    elif report.strategy == "stamp_head":
        command.stamp(cfg, HEAD_REVISION)
        command.upgrade(cfg, "head")
    elif report.strategy == "stamp_0003_upgrade":
        command.stamp(cfg, LINKS_REVISION)
        command.upgrade(cfg, "head")
    elif report.strategy == "stamp_0002_upgrade":
        command.stamp(cfg, STRAVA_REVISION)
        command.upgrade(cfg, "head")
    elif report.strategy == "stamp_0001_upgrade":
        command.stamp(cfg, BASE_REVISION)
        command.upgrade(cfg, "head")
    else:
        raise RuntimeError("Schema divergente; nenhuma alteracao aplicada.")
    get_settings.cache_clear()


def main() -> int:
    parser = argparse.ArgumentParser(description="Adota com seguranca um SQLite existente no Alembic.")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--yes", action="store_true", help="Confirma a execucao da adocao.")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()

    report = inspect_database(args.database)
    print(f"database={report.database_path}")
    print(f"alembic_version={report.alembic_version or 'none'}")
    print(f"strategy={report.strategy}")
    print(f"counts={report.counts}")
    print(f"fit_reference_samples={report.fit_reference_samples}")
    if report.table_differences:
        print(f"differences={report.table_differences}")
    if not args.yes:
        print("dry_run=true; use --yes para aplicar com backup.")
        return 0 if report.is_safe else 2

    after = adopt_database(args.database, confirm=True, backup_dir=args.backup_dir)
    print(f"applied=true strategy={after.strategy} alembic_version={after.alembic_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
