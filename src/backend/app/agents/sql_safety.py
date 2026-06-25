from __future__ import annotations

import re
from urllib.parse import unquote


class SqlSafetyError(ValueError):
    pass


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"ATTACH|DETACH|RENAME|OPTIMIZE|SYSTEM|KILL)\b",
    re.IGNORECASE,
)
_COMMENTS = re.compile(r"(--|#|/\*|\*/)")


def validate_readonly_sql(sql: str, limit: int = 100) -> str:
    candidate = unquote(str(sql or "")).strip().rstrip(";")
    if not candidate:
        raise SqlSafetyError("SQL is empty")
    if len(candidate) > 8000:
        raise SqlSafetyError("SQL exceeds the 8000 character limit")
    if ";" in candidate:
        raise SqlSafetyError("Multiple statements are not allowed")
    if _COMMENTS.search(candidate):
        raise SqlSafetyError("SQL comments are not allowed")
    if _FORBIDDEN.search(candidate):
        raise SqlSafetyError("Only read-only SELECT statements are allowed")
    if not re.match(r"^(SELECT|WITH)\b", candidate, re.IGNORECASE):
        raise SqlSafetyError("SQL must start with SELECT or WITH")
    if not re.search(r"\bLIMIT\b", candidate, re.IGNORECASE):
        candidate = f"{candidate} LIMIT {max(1, min(int(limit), 500))}"
    return candidate
