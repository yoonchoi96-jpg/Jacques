#!/usr/bin/env python3
"""Three-way, row-aware merge for concurrent SQLite database updates."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


_MISSING = object()


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _tables(conn):
    return {
        row[0]: row[1]
        for row in conn.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        )
        if row[1]
    }


def _columns(conn, table):
    return conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()


def _rows(conn, table, columns, key_columns):
    selected = ", ".join(_quote(column) for column in columns)
    result = {}
    for values in conn.execute(f"SELECT {selected} FROM {_quote(table)}"):
        row = dict(zip(columns, values))
        key = tuple(row[column] for column in key_columns)
        result[key] = row
    return result


def _same_on(row, other, columns):
    if row is None or other is None:
        return row is other
    return all(row.get(column, _MISSING) == other.get(column, _MISSING) for column in columns)


def _merge_value(base, runner, latest):
    if runner == latest:
        return runner
    if base is _MISSING:
        if latest is None:
            return runner
        if runner is None:
            return latest
        return latest
    if runner == base:
        return latest
    if latest == base:
        return runner
    if latest is None:
        return runner
    return latest


def _ensure_runner_schema(output, runner, table):
    output_tables = _tables(output)
    runner_tables = _tables(runner)
    if table not in output_tables:
        output.execute(runner_tables[table])
    output_columns = {row[1] for row in _columns(output, table)}
    for column in _columns(runner, table):
        name, declared_type, not_null, default, primary_key = (
            column[1], column[2], column[3], column[4], column[5]
        )
        if name in output_columns:
            continue
        if primary_key:
            raise RuntimeError(
                f"Cannot safely add primary-key column {table}.{name}"
            )
        definition = _quote(name)
        if declared_type:
            definition += f" {declared_type}"
        if default is not None:
            definition += f" DEFAULT {default}"
        elif not_null:
            raise RuntimeError(
                f"Cannot safely add required column {table}.{name} without a default"
            )
        output.execute(
            f"ALTER TABLE {_quote(table)} ADD COLUMN {definition}"
        )
        output_columns.add(name)


def _write_row(output, table, row, key_columns):
    columns = [column[1] for column in _columns(output, table)]
    names = ", ".join(_quote(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    values = [row.get(column) for column in columns]
    if key_columns:
        keys = ", ".join(_quote(column) for column in key_columns)
        updates = ", ".join(
            f"{_quote(column)} = excluded.{_quote(column)}"
            for column in columns if column not in key_columns
        )
        if updates:
            output.execute(
                f"INSERT INTO {_quote(table)} ({names}) VALUES ({placeholders}) "
                f"ON CONFLICT ({keys}) DO UPDATE SET {updates}",
                values,
            )
        else:
            output.execute(
                f"INSERT OR IGNORE INTO {_quote(table)} ({names}) "
                f"VALUES ({placeholders})",
                values,
            )
    else:
        output.execute(
            f"INSERT INTO {_quote(table)} ({names}) VALUES ({placeholders})",
            values,
        )


def _delete_row(output, table, key_columns, key):
    where = " AND ".join(f"{_quote(column)} = ?" for column in key_columns)
    output.execute(
        f"DELETE FROM {_quote(table)} WHERE {where}",
        key,
    )


def safe_merge(base_path, runner_path, latest_path, output_path):
    for path in (base_path, runner_path, latest_path):
        if not Path(path).is_file():
            raise FileNotFoundError(path)

    output_path = Path(output_path)
    if output_path.exists():
        output_path.unlink()

    with sqlite3.connect(latest_path) as latest_source, sqlite3.connect(
        output_path
    ) as output:
        latest_source.backup(output)

    base = sqlite3.connect(base_path)
    runner = sqlite3.connect(runner_path)
    latest = sqlite3.connect(latest_path)
    output = sqlite3.connect(output_path)
    try:
        output.execute("PRAGMA foreign_keys = OFF")
        runner_tables = _tables(runner)
        base_tables = _tables(base)

        for table in sorted(runner_tables):
            _ensure_runner_schema(output, runner, table)
            columns = [row[1] for row in _columns(output, table)]
            info = _columns(output, table)
            key_columns = [
                row[1] for row in sorted(info, key=lambda item: item[5]) if row[5]
            ]
            if not key_columns:
                raise RuntimeError(
                    f"Cannot safely merge table {table} without a primary key"
                )

            runner_columns = [row[1] for row in _columns(runner, table)]
            latest_columns = (
                [row[1] for row in _columns(latest, table)]
                if table in _tables(latest)
                else []
            )
            base_columns = (
                [row[1] for row in _columns(base, table)]
                if table in base_tables
                else []
            )
            runner_rows = _rows(runner, table, runner_columns, key_columns)
            latest_rows = (
                _rows(latest, table, latest_columns, key_columns)
                if latest_columns
                else {}
            )
            base_rows = (
                _rows(base, table, base_columns, key_columns)
                if base_columns
                else {}
            )
            comparable = set(base_columns)

            for key in runner_rows.keys() | base_rows.keys():
                before = base_rows.get(key)
                generated = runner_rows.get(key)
                current = latest_rows.get(key)
                runner_unchanged = _same_on(generated, before, comparable)
                if before and generated:
                    runner_unchanged = runner_unchanged and all(
                        generated.get(column) is None
                        for column in set(runner_columns) - comparable
                    )
                latest_unchanged = _same_on(current, before, comparable)

                if runner_unchanged:
                    chosen = current
                elif latest_unchanged:
                    chosen = generated
                elif generated is None:
                    chosen = current
                elif current is None:
                    chosen = generated
                else:
                    chosen = {}
                    for column in columns:
                        base_value = (
                            before.get(column, _MISSING)
                            if before is not None and column in base_columns
                            else _MISSING
                        )
                        chosen[column] = _merge_value(
                            base_value,
                            generated.get(column),
                            current.get(column),
                        )

                if chosen is None:
                    if key in latest_rows:
                        _delete_row(output, table, key_columns, key)
                elif not _same_on(chosen, current, columns):
                    _write_row(output, table, chosen, key_columns)

        output.commit()
        check = output.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise sqlite3.DatabaseError(f"SQLite integrity check failed: {check}")
        foreign_key_error = output.execute("PRAGMA foreign_key_check").fetchone()
        if foreign_key_error:
            raise sqlite3.DatabaseError(
                f"SQLite foreign-key check failed: {foreign_key_error}"
            )
    finally:
        output.close()
        latest.close()
        runner.close()
        base.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Database before the runner update")
    parser.add_argument("--runner", required=True, help="Database produced by the runner")
    parser.add_argument("--latest", required=True, help="Latest database on the target branch")
    parser.add_argument("--output", required=True, help="Merged database output path")
    args = parser.parse_args()
    safe_merge(args.base, args.runner, args.latest, args.output)


if __name__ == "__main__":
    main()
