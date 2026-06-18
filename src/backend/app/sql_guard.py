from __future__ import annotations


class SqlRejected(ValueError):
    pass


def validate_readonly_sql(sql: str) -> None:
    cleaned = sql.strip().rstrip(";").strip()
    lowered = cleaned.lower()
    if ";" in cleaned:
        raise SqlRejected("Only one SQL statement is allowed.")
    if not (lowered.startswith("select ") or lowered.startswith("show ") or lowered.startswith("describe ")):
        raise SqlRejected("Only read-only SELECT, SHOW, or DESCRIBE statements are allowed.")
