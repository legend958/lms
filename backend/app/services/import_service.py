"""CSV user import with per-row validation and atomic/partial modes."""

from __future__ import annotations

import csv
import io
import re
import secrets
import uuid
from dataclasses import dataclass, field

import structlog
from pydantic import EmailStr, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.admin import ImportReport, ImportRowResult
from app.core.errors import ValidationError
from app.core.roles import CourseRole, CourseStatus, Role, UserStatus
from app.core.security import hash_password
from app.db.models import Course, User
from app.db.repositories import courses as courses_repo
from app.services import audit_service

logger = structlog.get_logger(__name__)

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 1000
REQUIRED_COLUMNS = ("name", "email", "role")
OPTIONAL_COLUMNS = ("course_codes",)
EMAIL_ADAPTER: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)


@dataclass
class _Candidate:
    row_number: int
    name: str
    email: str
    role: Role
    course_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class _Parsed:
    rows: list[ImportRowResult]
    candidates: list[_Candidate]


def _decode(data: bytes) -> str:
    if not data:
        raise ValidationError("The uploaded CSV is empty")
    if len(data) > MAX_IMPORT_BYTES:
        raise ValidationError("The CSV must be 2 MB or smaller")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("The CSV must be UTF-8 encoded") from exc


def _split_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip().upper() for part in re.split(r"[;|]", raw) if part.strip()]


async def _parse(session: AsyncSession, data: bytes) -> _Parsed:
    text = _decode(data)
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValidationError("The CSV has no header row")

    headers = {name.strip().lower(): name for name in reader.fieldnames if name}
    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing:
        raise ValidationError(f"Missing required CSV column(s): {', '.join(missing)}")

    raw_rows: list[tuple[int, dict[str, str | None]]] = []
    for raw in reader:
        normalized = {
            (key or "").strip().lower(): (value.strip() if isinstance(value, str) else value)
            for key, value in raw.items()
            if key
        }
        if not any(normalized.get(column) for column in headers):
            continue
        raw_rows.append((reader.line_num, normalized))
    if len(raw_rows) > MAX_IMPORT_ROWS:
        raise ValidationError(f"The CSV may contain at most {MAX_IMPORT_ROWS} data rows")

    all_codes = {code for _, row in raw_rows for code in _split_codes(row.get("course_codes"))}
    course_map: dict[str, uuid.UUID] = {}
    if all_codes:
        course_rows = await session.execute(
            select(Course.code, Course.id).where(
                Course.code.in_(all_codes),
                Course.status == CourseStatus.ACTIVE,
            )
        )
        course_map = {code: course_id for code, course_id in course_rows.all()}

    emails = [
        str(row.get("email") or "").strip().lower() for _, row in raw_rows if row.get("email")
    ]
    existing: set[str] = set()
    if emails:
        existing = set(
            (await session.execute(select(User.email).where(User.email.in_(emails)))).scalars()
        )

    rows: list[ImportRowResult] = []
    candidates: list[_Candidate] = []
    seen: set[str] = set()

    for row_number, row in raw_rows:
        name = str(row.get("name") or "").strip()
        raw_email = str(row.get("email") or "").strip().lower()
        raw_role = str(row.get("role") or "").strip().lower()

        if not name or len(name) > 120:
            rows.append(
                ImportRowResult(
                    row_number=row_number,
                    email=raw_email or None,
                    status="error",
                    reason="Name must be between 1 and 120 characters",
                )
            )
            continue
        try:
            email = str(EMAIL_ADAPTER.validate_python(raw_email))
        except PydanticValidationError:
            rows.append(
                ImportRowResult(
                    row_number=row_number,
                    email=raw_email or None,
                    status="error",
                    reason="Invalid email address",
                )
            )
            continue
        try:
            role = Role(raw_role)
        except ValueError:
            rows.append(
                ImportRowResult(
                    row_number=row_number,
                    email=email,
                    status="error",
                    reason="Role must be student, teacher, or admin",
                )
            )
            continue

        if email in seen:
            rows.append(
                ImportRowResult(
                    row_number=row_number,
                    email=email,
                    status="skipped",
                    reason="Duplicate email in this file",
                )
            )
            continue
        seen.add(email)

        if email in existing:
            rows.append(
                ImportRowResult(
                    row_number=row_number,
                    email=email,
                    status="skipped",
                    reason="A user with this email already exists",
                )
            )
            continue

        codes = _split_codes(row.get("course_codes"))
        unknown = [code for code in codes if code not in course_map]
        if unknown:
            rows.append(
                ImportRowResult(
                    row_number=row_number,
                    email=email,
                    status="error",
                    reason=f"Unknown course code(s): {', '.join(unknown)}",
                )
            )
            continue

        candidates.append(
            _Candidate(
                row_number=row_number,
                name=name,
                email=email,
                role=role,
                course_ids=[course_map[code] for code in codes],
            )
        )

    return _Parsed(rows=rows, candidates=candidates)


async def import_users(
    session: AsyncSession,
    *,
    actor: User,
    data: bytes,
    mode: str,
    ip: str | None = None,
    request_id: str | None = None,
) -> ImportReport:
    if mode not in {"atomic", "partial"}:
        raise ValidationError("mode must be atomic or partial")

    parsed = await _parse(session, data)
    report_rows = sorted(parsed.rows, key=lambda item: item.row_number)
    failed = sum(1 for row in report_rows if row.status == "error")
    skipped = sum(1 for row in report_rows if row.status == "skipped")
    created_ids: list[str] = []

    if mode == "atomic" and failed:
        for candidate in parsed.candidates:
            report_rows.append(
                ImportRowResult(
                    row_number=candidate.row_number,
                    email=candidate.email,
                    status="skipped",
                    reason="Not imported because another row failed in atomic mode",
                )
            )
        skipped += len(parsed.candidates)
    else:
        try:
            for candidate in parsed.candidates:
                temporary_password = secrets.token_urlsafe(9)
                user = User(
                    name=candidate.name,
                    email=candidate.email,
                    password_hash=hash_password(temporary_password),
                    role=candidate.role,
                    status=UserStatus.ACTIVE,
                )
                try:
                    async with session.begin_nested():
                        session.add(user)
                        await session.flush()
                        course_role = (
                            CourseRole.TEACHER
                            if candidate.role is Role.TEACHER
                            else CourseRole.STUDENT
                        )
                        for course_id in candidate.course_ids:
                            await courses_repo.enroll(session, course_id, user.id, course_role)
                except IntegrityError:
                    if mode == "atomic":
                        raise
                    report_rows.append(
                        ImportRowResult(
                            row_number=candidate.row_number,
                            email=candidate.email,
                            status="skipped",
                            reason="A user with this email already exists",
                        )
                    )
                    skipped += 1
                    continue

                created_ids.append(str(user.id))
                report_rows.append(
                    ImportRowResult(
                        row_number=candidate.row_number,
                        email=candidate.email,
                        status="created",
                        user_id=user.id,
                        temporary_password=temporary_password,
                    )
                )
        except Exception:
            if mode == "atomic":
                await session.rollback()
                report_rows = [
                    ImportRowResult(
                        row_number=row.row_number,
                        email=row.email,
                        status="skipped" if row.status == "created" else row.status,
                        reason=(
                            "Import rolled back because an insert failed"
                            if row.status == "created"
                            else row.reason
                        ),
                    )
                    for row in report_rows
                ]
                created_ids = []
            else:
                raise

    if mode == "atomic" and failed:
        # No users were written, but the attempt is still privileged and visible.
        created_ids = []

    report_rows.sort(key=lambda item: item.row_number)
    created = sum(1 for row in report_rows if row.status == "created")
    failed = sum(1 for row in report_rows if row.status == "error")
    skipped = sum(1 for row in report_rows if row.status == "skipped")

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="user.import",
        entity_type="user",
        after={
            "mode": mode,
            "created": created,
            "skipped": skipped,
            "failed": failed,
            "user_ids": created_ids,
            "emails": [row.email for row in report_rows if row.status == "created" and row.email],
        },
        ip=ip,
        request_id=request_id,
    )
    await session.commit()

    logger.info(
        "user_import_complete",
        mode=mode,
        created=created,
        skipped=skipped,
        failed=failed,
        actor_id=str(actor.id),
    )
    return ImportReport(
        mode=mode,  # type: ignore[arg-type]
        total=len(report_rows),
        created=created,
        skipped=skipped,
        failed=failed,
        rows=report_rows,
    )


__all__ = ["import_users"]
