import hashlib
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Cookie, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path("/tmp/quiet-stage") if os.getenv("VERCEL") else BASE_DIR / "data"
DATA_DIR = Path(os.getenv("QUIET_STAGE_DATA_DIR", DEFAULT_DATA_DIR))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "quiet_stage.db"
MAX_AUDIO_SIZE = 15 * 1024 * 1024
ALLOWED_AUDIO = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
}
VALID_NEXT_STEPS = {
    "Доработать работу",
    "Показать пилотной группе",
    "Остаться в закрытом режиме",
    "Поставить на паузу",
}
TERMS_VERSION = "1.0-2026-09-24"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Тихая сцена API", version="1.0.0")
app.mount("/assets", StaticFiles(directory=BASE_DIR / "assets"), name="assets")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db():
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY,
                access_code_hash TEXT NOT NULL UNIQUE,
                alias TEXT NOT NULL,
                age_group TEXT NOT NULL,
                format TEXT NOT NULL,
                genre TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                audio_path TEXT,
                original_filename TEXT,
                status TEXT NOT NULL DEFAULT 'submitted',
                feedback TEXT,
                next_step TEXT,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                personal_data_consent_at TEXT,
                copyright_consent_at TEXT,
                guardian_consent_at TEXT,
                terms_version TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS moderator_sessions (
                token_hash TEXT PRIMARY KEY,
                csrf_token TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id TEXT,
                event TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(submission_id) REFERENCES submissions(id)
            );
            CREATE TABLE IF NOT EXISTS contact_requests (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                message TEXT NOT NULL,
                consent_at TEXT NOT NULL,
                terms_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS expert_applications (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                role TEXT NOT NULL,
                experience TEXT NOT NULL,
                portfolio TEXT,
                motivation TEXT NOT NULL,
                consent_at TEXT NOT NULL,
                terms_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(submissions)").fetchall()
        }
        for name in (
            "personal_data_consent_at",
            "copyright_consent_at",
            "guardian_consent_at",
            "terms_version",
        ):
            if name not in existing:
                connection.execute(f"ALTER TABLE submissions ADD COLUMN {name} TEXT")


@app.on_event("startup")
def startup():
    if not os.getenv("MODERATOR_PASSWORD"):
        raise RuntimeError("Set MODERATOR_PASSWORD before starting the server")
    init_db()


def public_submission(row):
    return {
        "id": row["id"],
        "alias": row["alias"],
        "ageGroup": row["age_group"],
        "format": row["format"],
        "genre": row["genre"],
        "title": row["title"],
        "body": row["body"],
        "hasAudio": bool(row["audio_path"]),
        "status": row["status"],
        "feedback": row["feedback"],
        "nextStep": row["next_step"],
        "createdAt": row["created_at"],
        "reviewedAt": row["reviewed_at"],
    }


def get_by_access_code(connection, access_code: str):
    if not access_code or len(access_code) > 100:
        return None
    return connection.execute(
        "SELECT * FROM submissions WHERE access_code_hash = ?",
        (hash_value(access_code.strip().upper()),),
    ).fetchone()


def require_moderator(session_token: str | None):
    if not session_token:
        raise HTTPException(401, "Требуется вход модератора")
    now = utcnow()
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM moderator_sessions WHERE token_hash = ? AND expires_at > ?",
            (hash_value(session_token), now),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Сессия истекла")
    return row


def verify_csrf(session, csrf_header: str | None):
    if not csrf_header or not secrets.compare_digest(session["csrf_token"], csrf_header):
        raise HTTPException(403, "Некорректный CSRF-токен")


class LoginPayload(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class ReviewPayload(BaseModel):
    feedback: str = Field(min_length=20, max_length=4000)


class NextStepPayload(BaseModel):
    accessCode: str = Field(min_length=8, max_length=100)
    nextStep: str


class ContactPayload(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=200)
    message: str = Field(min_length=10, max_length=3000)
    personalData: bool


class ExpertApplicationPayload(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=200)
    role: str = Field(min_length=2, max_length=100)
    experience: str = Field(min_length=20, max_length=3000)
    portfolio: str = Field(default="", max_length=500)
    motivation: str = Field(min_length=20, max_length=3000)
    personalData: bool


def clean_email(value: str) -> str:
    value = value.strip().lower()
    if value.count("@") != 1 or "." not in value.split("@", 1)[1] or any(char.isspace() for char in value):
        raise HTTPException(422, "Укажите корректный адрес электронной почты")
    return value


def clean_portfolio(value: str) -> str | None:
    value = value.strip()
    if value and not value.lower().startswith(("https://", "http://")):
        raise HTTPException(422, "Ссылка на портфолио должна начинаться с http:// или https://")
    return value or None


@app.get("/api/health")
def health():
    return {"status": "ok", "database": DB_PATH.exists()}


@app.post("/api/submissions", status_code=201)
async def create_submission(
    alias: str = Form(..., min_length=2, max_length=40),
    ageGroup: str = Form(...),
    format: str = Form(...),
    genre: str = Form("Другое", max_length=40),
    title: str = Form(..., min_length=1, max_length=100),
    body: str = Form("", max_length=5000),
    author: bool = Form(...),
    voluntary: bool = Form(...),
    personalData: bool = Form(...),
    copyrightTerms: bool = Form(...),
    guardianConsent: bool = Form(False),
    demo: bool = Form(...),
    audio: UploadFile | None = File(None),
):
    if ageGroup not in {"14–17 лет", "18–22 года"}:
        raise HTTPException(422, "Некорректная возрастная группа")
    if format not in {"Текст", "Аудио"}:
        raise HTTPException(422, "Некорректный формат")
    if not author or not voluntary or not personalData or not copyrightTerms or not demo:
        raise HTTPException(422, "Не подтверждены обязательные условия демонстрационного участия")
    if ageGroup == "14–17 лет" and not guardianConsent:
        raise HTTPException(422, "Для возрастной группы 14–17 лет требуется подтверждение согласия законного представителя")
    body = body.strip()
    audio_path = None
    original_filename = None
    if format == "Текст" and len(body) < 20:
        raise HTTPException(422, "Текст должен содержать не менее 20 символов")
    if format == "Аудио":
        if audio is None or not audio.filename:
            raise HTTPException(422, "Для аудиоработы требуется файл")
        if audio.content_type not in ALLOWED_AUDIO:
            raise HTTPException(415, "Разрешены MP3, M4A, WAV и OGG")
        content = await audio.read(MAX_AUDIO_SIZE + 1)
        if len(content) > MAX_AUDIO_SIZE:
            raise HTTPException(413, "Аудиофайл превышает 15 МБ")
        stored_name = f"{uuid.uuid4().hex}{ALLOWED_AUDIO[audio.content_type]}"
        destination = UPLOAD_DIR / stored_name
        destination.write_bytes(content)
        audio_path = stored_name
        original_filename = Path(audio.filename).name[:200]
    submission_id = uuid.uuid4().hex
    access_code = f"TS-{secrets.token_hex(4).upper()}"
    now = utcnow()
    with db() as connection:
        connection.execute(
            """INSERT INTO submissions
            (id, access_code_hash, alias, age_group, format, genre, title, body, audio_path,
             original_filename, status, created_at, personal_data_consent_at,
             copyright_consent_at, guardian_consent_at, terms_version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?, ?, ?)""",
            (submission_id, hash_value(access_code), alias.strip(), ageGroup, format, genre.strip(),
             title.strip(), body or None, audio_path, original_filename, now, now, now,
             now if guardianConsent else None, TERMS_VERSION, now),
        )
        connection.execute(
            "INSERT INTO audit_log(submission_id, event, created_at) VALUES (?, ?, ?)",
            (submission_id, "submission_created", now),
        )
    return {"accessCode": access_code, "message": "Работа передана модератору"}


@app.get("/api/submissions/status/{access_code}")
def submission_status(access_code: str):
    with db() as connection:
        row = get_by_access_code(connection, access_code)
    if not row:
        raise HTTPException(404, "Работа с таким кодом не найдена")
    return public_submission(row)


@app.post("/api/submissions/next-step")
def choose_next_step(payload: NextStepPayload):
    if payload.nextStep not in VALID_NEXT_STEPS:
        raise HTTPException(422, "Недопустимый следующий шаг")
    with db() as connection:
        row = get_by_access_code(connection, payload.accessCode)
        if not row:
            raise HTTPException(404, "Работа с таким кодом не найдена")
        if row["status"] == "submitted":
            raise HTTPException(409, "Сначала дождитесь отзыва модератора")
        status = "shown" if payload.nextStep == "Показать пилотной группе" else "reviewed"
        connection.execute(
            "UPDATE submissions SET next_step = ?, status = ?, updated_at = ? WHERE id = ?",
            (payload.nextStep, status, utcnow(), row["id"]),
        )
        connection.execute(
            "INSERT INTO audit_log(submission_id, event, created_at) VALUES (?, ?, ?)",
            (row["id"], f"next_step:{payload.nextStep}", utcnow()),
        )
    return {"message": "Следующий шаг сохранен"}


@app.post("/api/contact", status_code=201)
def create_contact_request(payload: ContactPayload):
    if not payload.personalData:
        raise HTTPException(422, "Необходимо согласие на обработку персональных данных")
    now = utcnow()
    with db() as connection:
        connection.execute(
            "INSERT INTO contact_requests(id, name, email, message, consent_at, terms_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, payload.name.strip(), clean_email(payload.email), payload.message.strip(), now, TERMS_VERSION, now),
        )
    return {"message": "Вопрос отправлен. Ответ придет на указанную почту"}


@app.post("/api/expert-applications", status_code=201)
def create_expert_application(payload: ExpertApplicationPayload):
    if not payload.personalData:
        raise HTTPException(422, "Необходимо согласие на обработку персональных данных")
    now = utcnow()
    with db() as connection:
        connection.execute(
            """INSERT INTO expert_applications
            (id, name, email, role, experience, portfolio, motivation, consent_at, terms_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex, payload.name.strip(), clean_email(payload.email), payload.role.strip(),
             payload.experience.strip(), clean_portfolio(payload.portfolio), payload.motivation.strip(), now, TERMS_VERSION, now),
        )
    return {"message": "Заявка эксперта отправлена на рассмотрение"}


@app.post("/api/moderator/login")
def moderator_login(payload: LoginPayload, response: Response):
    expected = os.environ["MODERATOR_PASSWORD"]
    if not secrets.compare_digest(payload.password, expected):
        raise HTTPException(401, "Неверный пароль")
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=8)
    with db() as connection:
        connection.execute("DELETE FROM moderator_sessions WHERE expires_at <= ?", (now.isoformat(),))
        connection.execute(
            "INSERT INTO moderator_sessions(token_hash, csrf_token, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (hash_value(token), csrf, expires.isoformat(), now.isoformat()),
        )
    response.set_cookie(
        "moderator_session", token, max_age=8 * 3600, httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true", samesite="strict",
    )
    return {"message": "Вход выполнен", "csrfToken": csrf}


@app.post("/api/moderator/logout")
def moderator_logout(response: Response, moderator_session: str | None = Cookie(None)):
    if moderator_session:
        with db() as connection:
            connection.execute("DELETE FROM moderator_sessions WHERE token_hash = ?", (hash_value(moderator_session),))
    response.delete_cookie("moderator_session")
    return {"message": "Выход выполнен"}


@app.get("/api/moderator/submissions")
def moderator_submissions(moderator_session: str | None = Cookie(None)):
    session = require_moderator(moderator_session)
    with db() as connection:
        rows = connection.execute("SELECT * FROM submissions ORDER BY created_at DESC").fetchall()
        contacts = connection.execute("SELECT * FROM contact_requests ORDER BY created_at DESC").fetchall()
        experts = connection.execute("SELECT * FROM expert_applications ORDER BY created_at DESC").fetchall()
    return {
        "csrfToken": session["csrf_token"],
        "items": [public_submission(row) for row in rows],
        "contactRequests": [dict(row) for row in contacts],
        "expertApplications": [dict(row) for row in experts],
    }


@app.post("/api/moderator/submissions/{submission_id}/review")
def review_submission(
    submission_id: str,
    payload: ReviewPayload,
    request: Request,
    moderator_session: str | None = Cookie(None),
):
    session = require_moderator(moderator_session)
    verify_csrf(session, request.headers.get("X-CSRF-Token"))
    now = utcnow()
    with db() as connection:
        row = connection.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Работа не найдена")
        connection.execute(
            "UPDATE submissions SET feedback = ?, status = 'reviewed', reviewed_at = ?, updated_at = ? WHERE id = ?",
            (payload.feedback.strip(), now, now, submission_id),
        )
        connection.execute(
            "INSERT INTO audit_log(submission_id, event, created_at) VALUES (?, ?, ?)",
            (submission_id, "moderator_reviewed", now),
        )
    return {"message": "Отзыв отправлен автору"}


@app.get("/api/moderator/submissions/{submission_id}/audio")
def moderator_audio(submission_id: str, moderator_session: str | None = Cookie(None)):
    require_moderator(moderator_session)
    with db() as connection:
        row = connection.execute("SELECT audio_path, original_filename FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    if not row or not row["audio_path"]:
        raise HTTPException(404, "Аудиофайл не найден")
    path = UPLOAD_DIR / row["audio_path"]
    if not path.is_file():
        raise HTTPException(404, "Аудиофайл отсутствует в хранилище")
    return FileResponse(path, filename=row["original_filename"] or path.name)


@app.get("/", include_in_schema=False)
def frontend_index():
    return FileResponse(BASE_DIR / "index.html", media_type="text/html")


@app.get("/styles.css", include_in_schema=False)
def frontend_styles():
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css")


@app.get("/server.css", include_in_schema=False)
def frontend_server_styles():
    return FileResponse(BASE_DIR / "server.css", media_type="text/css")


@app.get("/redesign.css", include_in_schema=False)
def frontend_redesign_styles():
    return FileResponse(BASE_DIR / "redesign.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
def frontend_script():
    return FileResponse(BASE_DIR / "app.js", media_type="text/javascript")
