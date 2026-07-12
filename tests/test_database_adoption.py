import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine

from alembic import command
from app import models  # noqa: F401
from app.db import Base
from scripts.adopt_existing_database import (
    HEAD_REVISION,
    adopt_database,
    inspect_database,
)


def make_db(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()


def alembic_version(path: Path) -> str | None:
    with sqlite3.connect(path) as con:
        tables = {
            row[0]
            for row in con.execute(
                "select name from sqlite_master where type=char(116,97,98,108,101)"
            )
        }
        if "alembic_version" not in tables:
            return None
        row = con.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else None


def insert_reference_counts(path: Path, samples: int = 1570) -> None:
    with sqlite3.connect(path) as con:
        con.execute("insert into users (id, name, timezone, created_at) values (1, 'Humberto', 'America/Sao_Paulo', '2026-07-11 00:00:00')")
        con.execute("insert into data_sources (id, name, kind, created_at) values (1, 'garmin', 'fit', '2026-07-11 00:00:00')")
        con.execute(
            """
            insert into activities
            (id, user_id, data_source_id, external_id, activity_type, started_at,
             total_duration_seconds, distance_meters, duplicate_status, created_at)
            values (1, 1, 1, 'fit-ref', 'run', '2026-07-11 12:42:05', 1569, 4004.25, 'unique', '2026-07-11 00:00:00')
            """
        )
        con.executemany(
            "insert into activity_samples (activity_id, recorded_at, heart_rate) values (1, ?, 157)",
            [(f"2026-07-11 12:{42 + index // 60:02d}:{5 + index % 60:02d}",) for index in range(samples)],
        )
        con.commit()


def test_schema_equivalent_without_alembic_version_uses_safe_stamp(tmp_path):
    db_path = tmp_path / "equivalent.db"
    make_db(db_path)
    report = inspect_database(db_path)
    assert report.alembic_version is None
    assert report.strategy == "stamp_head"


def test_safe_stamp_then_upgrade_preserves_counts_and_creates_backup(tmp_path, monkeypatch):
    monkeypatch.chdir(Path.cwd())
    db_path = tmp_path / "equivalent.db"
    backup_dir = tmp_path / "backups"
    make_db(db_path)
    insert_reference_counts(db_path)
    after = adopt_database(db_path, confirm=True, backup_dir=backup_dir)
    assert after.alembic_version == HEAD_REVISION
    assert after.counts["activities"] == 1
    assert after.counts["activity_samples"] == 1570
    assert after.fit_reference_samples == 1570
    assert list(backup_dir.glob("*.db"))


def test_partial_schema_stamps_0002_and_upgrades(tmp_path, monkeypatch):
    monkeypatch.chdir(Path.cwd())
    db_path = tmp_path / "partial.db"
    make_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute("drop table activity_source_links")
    report = inspect_database(db_path)
    assert report.strategy == "stamp_0002_upgrade"
    after = adopt_database(db_path, confirm=True, backup_dir=tmp_path / "backups")
    assert after.alembic_version == HEAD_REVISION
    assert "activity_source_links" in after.tables


def test_schema_divergence_is_detected(tmp_path):
    db_path = tmp_path / "divergent.db"
    make_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute("drop table integration_states")
        con.execute("create table integration_states (id integer primary key)")
    report = inspect_database(db_path)
    assert report.strategy == "divergent"
    with pytest.raises(RuntimeError):
        adopt_database(db_path, confirm=True, backup_dir=tmp_path / "backups")


def test_repeated_execution_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(Path.cwd())
    db_path = tmp_path / "idempotent.db"
    make_db(db_path)
    first = adopt_database(db_path, confirm=True, backup_dir=tmp_path / "backups")
    second = adopt_database(db_path, confirm=True, backup_dir=tmp_path / "backups")
    assert first.alembic_version == second.alembic_version == HEAD_REVISION


def test_confirmation_is_required_before_backup_or_stamp(tmp_path):
    db_path = tmp_path / "confirm.db"
    make_db(db_path)
    with pytest.raises(RuntimeError):
        adopt_database(db_path, confirm=False, backup_dir=tmp_path / "backups")
    assert not (tmp_path / "backups").exists()


def test_new_database_still_migrates_to_head(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'new.db').as_posix()}")
    from app.config import get_settings

    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    assert alembic_version(tmp_path / "new.db") == HEAD_REVISION
    get_settings.cache_clear()
