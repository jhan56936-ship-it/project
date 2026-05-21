import hashlib
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import certifi

# macOS python.org builds ship without a system cert bundle, which makes the
# Lyria/Gemini TLS handshakes fail with "unable to get local issuer certificate".
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import asyncio
import shutil

from audio_cache import generate_all_segments
from auth import extract_bearer, verify_google_token
from security import (
    is_valid_book_id,
    rate_limit,
    rate_limit_identity,
    verify_google_token_cached,
)
from ask_book import ask_book
from chapter_image import generate_chapter_image
from chapter_summarizer import summarize_all_chapters
from characters_extractor import extract_characters
from cover_generator import generate_cover
from dict_lookup import lookup_word
from tts_generator import generate_page_tts
from db import (
    add_favorite,
    add_highlight,
    add_reading_session,
    add_review,
    book_exists,
    delete_book as db_delete_book,
    delete_highlight as db_delete_highlight,
    get_all_progress,
    get_book,
    get_chapter_summaries,
    get_characters,
    get_favorites,
    get_moods,
    get_progress,
    get_reading_stats,
    get_toc,
    set_characters,
    init_db,
    list_books,
    list_highlights,
    list_reviews,
    remove_favorite,
    set_audio_status,
    set_book_visibility,
    set_chapter_summary,
    set_progress,
    set_toc,
    update_book_metadata,
    update_description,
    upsert_book,
)
from epub_parser import extract_epub
from mood_analyzer import analyze_pages
from pdf_parser import extract_pages, extract_toc, normalize_cover_image, render_thumbnail
from toc_analyzer import analyze_toc
from ws_handler import MusicSession

load_dotenv()

STORAGE = Path(__file__).parent / "storage"
STORAGE.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # Self-heal: any book whose audio cache never finished gets re-queued.
    for book in list_books():
        if book.get("audio_status") in (None, "pending", "generating", "failed"):
            moods = get_moods(book["id"])
            if moods:
                asyncio.create_task(_generate_audio_background(book["id"], moods))
                print(f"[startup] re-queued audio generation for {book['id']}")
    yield


app = FastAPI(title="Book Background Music API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Hard limit on a single uploaded file. Holds an entire PDF in memory for
# parsing, so we cap to keep RAM bounded and stop trivial DoS attempts.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Defense-in-depth response headers.

    - CSP locks down where scripts/iframes/connections can come from.
      `frame-src` includes Spline so the 3D hero scenes still load;
      `connect-src` includes wss for the Lyria WebSocket; `img-src` allows
      `data:`/`blob:` for in-memory previews (chapter images, AI covers).
    - X-Frame-Options stops anyone embedding our reader in a clickjack
      iframe.
    - X-Content-Type-Options blocks MIME sniffing (a PDF served with a
      wrong type can't be reinterpreted as HTML/script).
    - Referrer-Policy keeps the full URL (which carries `?token=` and
      `?cid=` query params) out of cross-origin Referer headers.
    - Permissions-Policy is a belt-and-suspenders deny list for sensitive
      browser APIs we never use.
    """
    response = await call_next(request)
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://accounts.google.com https://apis.google.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob: https://lh3.googleusercontent.com; "
        "connect-src 'self' ws: wss: https://accounts.google.com; "
        "frame-src https://my.spline.design https://accounts.google.com; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    return response


# Match /books/{book_id} and /books/{book_id}/anything (but not bare /books).
# The id segment is what we hand to get_book; sub-resource path is whatever
# follows.
_BOOK_PATH_RE = re.compile(r"^/books/([^/]+)(?:/|$)")


@app.middleware("http")
async def enforce_book_access(request: Request, call_next):
    """Central guard for every /books/{id}/... request.

    Why this exists: dozens of endpoints serve book content (PDF, text, TOC,
    chapter images, TTS, summaries, characters, ask). Adding a per-endpoint
    Depends to each is invasive and easy to forget on the next new route.
    A single path-matching middleware fails closed by default — any future
    book endpoint inherits the check automatically.

    Behavior:
    - Bare `/books` (the listing) is filtered inside the handler, not here.
    - CORS preflights pass through.
    - 404 (not 403) on access denial so book IDs aren't enumerable by
      probing for "exists but forbidden" vs "doesn't exist".
    """
    if request.method == "OPTIONS":
        return await call_next(request)
    m = _BOOK_PATH_RE.match(request.url.path)
    if not m:
        return await call_next(request)
    book_id = m.group(1)
    # Reject malformed IDs before hitting the DB. Stops path-traversal probes
    # and saves a query for obvious junk.
    if not is_valid_book_id(book_id):
        return JSONResponse({"detail": "Book not found"}, status_code=404)
    book = get_book(book_id)
    if not book:
        return JSONResponse({"detail": "Book not found"}, status_code=404)
    # Identity can arrive via headers (AJAX) OR query params (static URLs
    # like <img src> / <a href> that can't set custom headers).
    qp = request.query_params
    token = extract_bearer(request.headers.get("authorization")) or qp.get("token")
    user = verify_google_token_cached(token) if token else None
    client_id = request.headers.get("x-client-id") or qp.get("cid")
    if not book_visible_to(book, user, client_id):
        return JSONResponse({"detail": "Book not found"}, status_code=404)
    return await call_next(request)


async def current_user(authorization: str | None = Header(default=None)) -> dict | None:
    """Optional auth — returns Google claims if a valid token is present,
    else None. Route handlers decide whether to require it."""
    token = extract_bearer(authorization)
    if not token:
        return None
    return verify_google_token(token)


def require_user(user: dict | None = Depends(current_user)) -> dict:
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다")
    return user


def reader_identifier(
    user: dict | None = Depends(current_user),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> str:
    """Stable per-reader key for bookmarks/highlights. Uses Google email
    when logged in, otherwise a UUID the frontend stores in localStorage."""
    if user and user.get("email"):
        return f"user:{user['email']}"
    return f"anon:{x_client_id or 'unknown'}"


def _is_owner(book: dict, user: dict | None, client_id: str | None) -> bool:
    """The uploader can always access. Matches by Google email when signed in,
    otherwise by the anonymous X-Client-Id captured at upload time."""
    email = (user or {}).get("email")
    if email and book.get("uploader_email") and email == book["uploader_email"]:
        return True
    cid = (client_id or "").strip()
    if cid and book.get("uploader_client_id") and cid == book["uploader_client_id"]:
        return True
    # Legacy books predate ownership tracking — leave them open for now so
    # existing libraries don't break. New uploads always have an owner.
    if not book.get("uploader_email") and not book.get("uploader_client_id"):
        return True
    return False


def book_visible_to(book: dict, user: dict | None, client_id: str | None) -> bool:
    """A book is visible if it's public OR the requester owns it."""
    if book.get("visibility") == "public":
        return True
    return _is_owner(book, user, client_id)


def require_book_access(
    book_id: str,
    user: dict | None = Depends(current_user),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> dict:
    """Fetch a book and enforce access. Returns the book dict on success.
    404 for unknown book IDs; 403 when the requester isn't allowed to see it.
    Using 404 vs 403 deliberately: existence is itself sensitive — we don't
    want to confirm a book ID exists to non-owners."""
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    if not book_visible_to(book, user, x_client_id):
        raise HTTPException(404, "Book not found")
    return book


def require_book_owner(
    book_id: str,
    user: dict | None = Depends(current_user),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> dict:
    """Stricter than access: must be the uploader (used for mutations)."""
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    if not _is_owner(book, user, x_client_id):
        raise HTTPException(403, "이 책을 수정할 권한이 없습니다")
    return book


def _enforce_rate_limit(
    bucket: str,
    *,
    limit: int,
    window_seconds: float,
    user: dict | None,
    client_id: str | None,
) -> None:
    """Raise 429 if the caller has exceeded `limit` actions per window."""
    key = f"{bucket}:{rate_limit_identity(user, client_id)}"
    wait = rate_limit(key, limit=limit, window_seconds=window_seconds)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"요청이 너무 많아요. {int(wait + 1)}초 뒤에 다시 시도해주세요.",
            headers={"Retry-After": str(int(wait + 1))},
        )


def rate_limit_ai(
    user: dict | None = Depends(current_user),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> None:
    """Text-only AI calls (ask, lookup, dict, characters extraction).
    Cheap per call but easy to abuse — 20/min per caller."""
    _enforce_rate_limit("ai", limit=20, window_seconds=60.0,
                        user=user, client_id=x_client_id)


def rate_limit_image(
    user: dict | None = Depends(current_user),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> None:
    """Image generation (chapter images, AI covers). Each call hits Nano
    Banana / Imagen — much costlier — so a stricter 5/min cap."""
    _enforce_rate_limit("img", limit=5, window_seconds=60.0,
                        user=user, client_id=x_client_id)


def rate_limit_upload(
    user: dict | None = Depends(current_user),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> None:
    """Uploads kick off chapter summarization + mood analysis + audio
    cache, so they're heavy. 3/min per caller is plenty."""
    _enforce_rate_limit("upload", limit=3, window_seconds=60.0,
                        user=user, client_id=x_client_id)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "books": len(list_books())}


@app.get("/me")
def me(user: dict | None = Depends(current_user)) -> dict:
    if not user:
        return {"signedIn": False}
    return {
        "signedIn": True,
        "email": user.get("email"),
        "name": user.get("name"),
        "picture": user.get("picture"),
    }


ACCEPTED_COVER_MIME = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}


async def _read_cover(cover: UploadFile | None) -> bytes | None:
    """Pull and validate an uploaded cover image, returning normalized PNG bytes."""
    if cover is None:
        return None
    if not getattr(cover, "filename", None):
        return None  # field present but empty
    if cover.content_type and cover.content_type not in ACCEPTED_COVER_MIME:
        raise HTTPException(400, f"Unsupported cover image type: {cover.content_type}")
    raw = await cover.read()
    if not raw:
        return None
    try:
        return normalize_cover_image(raw)
    except Exception as exc:
        raise HTTPException(400, f"Could not read cover image: {exc}")


def _detect_format(file: UploadFile, data: bytes) -> str:
    """Return 'epub' for EPUB uploads, otherwise 'pdf'."""
    ct = (file.content_type or "").lower()
    fn = (file.filename or "").lower()
    if "epub" in ct or fn.endswith(".epub"):
        return "epub"
    # Magic bytes: EPUB is a ZIP starting with PK\x03\x04.
    if data[:2] == b"PK":
        return "epub"
    return "pdf"


@app.post("/upload")
async def upload_book(
    file: UploadFile,
    description: str | None = Form(default=None),
    title_override: str | None = Form(default=None),
    author: str | None = Form(default=None),
    category: str | None = Form(default=None),
    subtitle: str | None = Form(default=None),
    translator: str | None = Form(default=None),
    publisher: str | None = Form(default=None),
    published_year: str | None = Form(default=None),  # str → int parsed below
    language: str | None = Form(default=None),
    isbn: str | None = Form(default=None),
    series_name: str | None = Form(default=None),
    series_index: str | None = Form(default=None),
    tags: str | None = Form(default=None),  # comma-separated
    cover: UploadFile | None = None,
    user: dict | None = Depends(current_user),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    _rl: None = Depends(rate_limit_upload),
) -> dict:
    def _clean(s: str | None) -> str | None:
        return s.strip() if s and s.strip() else None

    def _parse_year(s: str | None) -> int | None:
        if not s or not s.strip():
            return None
        try:
            y = int(s.strip())
            return y if 1 <= y <= 9999 else None
        except ValueError:
            return None

    def _parse_tags(s: str | None) -> list[str]:
        if not s:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for tok in s.split(","):
            t = tok.strip()
            low = t.lower()
            if t and low not in seen and len(t) <= 40:
                seen.add(low)
                out.append(t)
            if len(out) >= 20:
                break
        return out

    extras = {
        "subtitle": _clean(subtitle),
        "translator": _clean(translator),
        "publisher": _clean(publisher),
        "published_year": _parse_year(published_year),
        "language": _clean(language),
        "isbn": _clean(isbn),
        "series_name": _clean(series_name),
        "series_index": _clean(series_index),
        "tags": _parse_tags(tags),
    }
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        # 413 = Payload Too Large. Reject after the read because Starlette
        # streams uploads to disk and we can't enforce a hard cap upstream.
        raise HTTPException(
            413,
            f"파일이 너무 큽니다 (최대 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)",
        )

    book_format = _detect_format(file, data)
    book_id = hashlib.sha256(data).hexdigest()[:16]
    desc = (description or "").strip() or None
    cover_png = await _read_cover(cover)

    if book_exists(book_id):
        update_book_metadata(
            book_id,
            title=_clean(title_override),
            author=_clean(author),
            category=_clean(category),
            description=desc,
            **extras,
        )
        if cover_png is not None:
            (STORAGE / f"{book_id}.thumb.png").write_bytes(cover_png)
        book = get_book(book_id)
        assert book is not None
        return {"book_id": book_id, "page_count": book["page_count"], "cached": True}

    if book_format == "epub":
        try:
            epub_data = extract_epub(data)
        except Exception as exc:
            raise HTTPException(400, f"EPUB 파싱 실패: {exc}")
        pages = epub_data["pages"]
        if not pages:
            raise HTTPException(400, "EPUB has no readable text")
        embedded_toc = epub_data["toc"]
        ebook_cover_bytes = epub_data.get("cover_bytes")
        ebook_title = epub_data.get("title") or ""
    else:
        if file.content_type not in {
            "application/pdf",
            "application/octet-stream",
            "",
            None,
        }:
            raise HTTPException(
                400, f"Unsupported content-type: {file.content_type}"
            )
        pages = extract_pages(data)
        if not pages:
            raise HTTPException(400, "PDF has no pages")
        embedded_toc = extract_toc(data)
        ebook_cover_bytes = None
        ebook_title = ""

    if embedded_toc:
        moods = await analyze_pages(pages)
        toc = embedded_toc
    else:
        moods, toc = await asyncio.gather(analyze_pages(pages), analyze_toc(pages))

    # Persist source bytes (.pdf or .epub).
    src_path = STORAGE / f"{book_id}.{book_format}"
    src_path.write_bytes(data)

    # Cover priority: user-uploaded > EPUB-embedded > PDF first-page render.
    thumb_path = STORAGE / f"{book_id}.thumb.png"
    if cover_png is not None:
        thumb_path.write_bytes(cover_png)
    elif ebook_cover_bytes:
        try:
            thumb_path.write_bytes(normalize_cover_image(ebook_cover_bytes))
        except Exception as exc:
            print(f"[main] epub cover normalize failed: {exc!r}")
    elif book_format == "pdf":
        try:
            thumb_path.write_bytes(render_thumbnail(data))
        except Exception as exc:
            print(f"[main] thumbnail render failed: {exc!r}")

    fallback_name = file.filename or f"untitled-{book_id}.{book_format}"
    title = (title_override or ebook_title or fallback_name).strip()
    uploader_email = (user or {}).get("email")
    uploader_name = (user or {}).get("name")
    upsert_book(
        book_id,
        title,
        len(pages),
        len(data),
        moods,
        description=desc,
        uploader_email=uploader_email,
        uploader_name=uploader_name,
        book_format=book_format,
        author=_clean(author),
        category=_clean(category),
        # Newly uploaded books are private by default — only the uploader
        # (matched by Google email or anon client-id) can access them.
        visibility="private",
        uploader_client_id=(x_client_id or "").strip() or None,
        **extras,
    )
    set_toc(book_id, toc)

    # Kick off Lyria audio generation in the background so future plays stream
    # from the disk cache instead of reopening a Lyria session each time.
    asyncio.create_task(_generate_audio_background(book_id, moods))
    # And chapter summaries (only meaningful if the book has a real TOC).
    if toc:
        asyncio.create_task(_generate_summaries_background(book_id, toc, pages))

    return {"book_id": book_id, "page_count": len(pages), "cached": False}


async def _generate_summaries_background(book_id: str, toc: list[dict], pages: list[str]) -> None:
    try:
        summaries = await summarize_all_chapters(toc, pages)
        for page, summary in summaries.items():
            set_chapter_summary(book_id, page, summary)
        print(f"[summarizer] book {book_id}: {len(summaries)} chapter summaries saved")
    except Exception as exc:
        print(f"[summarizer] book {book_id} failed: {exc!r}")


async def _generate_audio_background(book_id: str, moods: list[dict]) -> None:
    set_audio_status(book_id, "generating")
    try:
        segments = await generate_all_segments(book_id, moods)
        set_audio_status(book_id, "ready", segments)
        print(f"[audio_cache] book {book_id}: {len(segments)} segments ready")
    except Exception as exc:
        print(f"[audio_cache] book {book_id} failed: {exc!r}")
        set_audio_status(book_id, "failed")


@app.get("/books")
def list_all_books(
    identifier: str = Depends(reader_identifier),
    user: dict | None = Depends(current_user),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> dict:
    # Filter at the gateway: each requester only sees books they own (or
    # public ones). Stops the library from exposing other users' uploads.
    books = [b for b in list_books() if book_visible_to(b, user, x_client_id)]
    progress_map = get_all_progress(identifier)
    favs = get_favorites(identifier)
    for b in books:
        prog = progress_map.get(b["id"])
        if prog:
            b["last_page"] = prog["page"]
            b["last_read_at"] = prog["updated_at"]
        b["favorited"] = b["id"] in favs
        # Don't leak internal owner identifiers in the listing.
        b.pop("uploader_client_id", None)
    return {"books": books}


@app.post("/books/{book_id}/favorite")
def add_favorite_endpoint(
    book_id: str,
    identifier: str = Depends(reader_identifier),
) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    add_favorite(identifier, book_id)
    return {"favorited": True}


@app.delete("/books/{book_id}/favorite", status_code=204)
def remove_favorite_endpoint(
    book_id: str,
    identifier: str = Depends(reader_identifier),
) -> Response:
    remove_favorite(identifier, book_id)
    return Response(status_code=204)


class SessionIn(BaseModel):
    book_id: str
    seconds: int = Field(ge=1, le=14400)  # 0-4 hours per session


@app.post("/me/sessions", status_code=201)
def record_session(
    body: SessionIn,
    identifier: str = Depends(reader_identifier),
) -> dict:
    if not book_exists(body.book_id):
        raise HTTPException(404, "Book not found")
    add_reading_session(identifier, body.book_id, body.seconds)
    return {"ok": True}


@app.get("/me/stats")
def me_stats(identifier: str = Depends(reader_identifier)) -> dict:
    return get_reading_stats(identifier)


@app.get("/books/{book_id}/characters")
async def get_book_characters(book_id: str) -> dict:
    """Returns the cached character/relationship map. Generates it on first
    request — one Gemini call per book, then served from DB forever."""
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    cached = get_characters(book_id)
    if cached is not None:
        return cached

    fmt = book.get("format") or "pdf"
    src = STORAGE / f"{book_id}.{fmt}"
    if not src.exists():
        raise HTTPException(404, f"{fmt.upper()} file not found")
    pages = (
        extract_epub(src.read_bytes())["pages"]
        if fmt == "epub"
        else extract_pages(src.read_bytes())
    )
    try:
        data = await extract_characters(pages)
    except Exception as exc:
        print(f"[characters] {book_id} failed: {exc!r}")
        raise HTTPException(503, f"Character extraction failed: {exc}")
    set_characters(book_id, data)
    return data


@app.get("/books/{book_id}/recap")
def get_recap(
    book_id: str,
    identifier: str = Depends(reader_identifier),
) -> dict:
    """Build a "previously on…" summary from cached chapter summaries up to
    (and including) the chapter that contains the reader's last page.
    No new Gemini call — purely a JOIN of existing data."""
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    prog = get_progress(book_id, identifier)
    last_page = (prog or {}).get("page") or 1
    if last_page <= 1:
        return {"last_page": last_page, "current_chapter": None, "previously": []}

    toc = get_toc(book_id) or []
    summaries = get_chapter_summaries(book_id)

    # Find the chapter the user was in: highest TOC entry with page <= last_page.
    sorted_toc = sorted(toc, key=lambda e: int(e.get("page", 0)))
    current_chapter = None
    previously: list[dict] = []
    for entry in sorted_toc:
        page = int(entry.get("page", 0))
        if page > last_page:
            break
        item = {
            "page": page,
            "title": entry.get("title", "").strip(),
            "summary": summaries.get(page, ""),
        }
        previously.append(item)
        current_chapter = item

    return {
        "last_page": last_page,
        "last_read_at": (prog or {}).get("updated_at"),
        "current_chapter": current_chapter,
        "previously": previously,
    }


@app.get("/books/{book_id}")
def get_book_meta(book_id: str) -> dict:
    book = get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found")
    return book


class BookPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    subtitle: str | None = Field(default=None, max_length=300)
    translator: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    published_year: int | None = Field(default=None, ge=1, le=9999)
    language: str | None = Field(default=None, max_length=40)
    isbn: str | None = Field(default=None, max_length=40)
    series_name: str | None = Field(default=None, max_length=200)
    series_index: str | None = Field(default=None, max_length=20)
    tags: list[str] | None = Field(default=None, max_length=20)
    visibility: str | None = Field(default=None, pattern="^(private|public)$")


class ReviewIn(BaseModel):
    author: str | None = Field(default=None, max_length=40)
    rating: int = Field(ge=1, le=5)
    text: str = Field(min_length=1, max_length=2000)


class CoverGenIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="", max_length=80)


@app.patch("/books/{book_id}")
def patch_book(
    book_id: str,
    body: BookPatch,
    _owner: dict = Depends(require_book_owner),
) -> dict:
    payload: dict = {}
    for name in (
        "title", "author", "category", "description",
        "subtitle", "translator", "publisher", "language",
        "isbn", "series_name", "series_index",
    ):
        v = getattr(body, name)
        if v is not None:
            payload[name] = v.strip()
    if body.published_year is not None:
        payload["published_year"] = body.published_year
    if body.tags is not None:
        seen: set[str] = set()
        cleaned: list[str] = []
        for t in body.tags:
            t = (t or "").strip()
            low = t.lower()
            if t and low not in seen and len(t) <= 40:
                seen.add(low)
                cleaned.append(t)
        payload["tags"] = cleaned
    update_book_metadata(book_id, **payload)
    if body.visibility is not None:
        set_book_visibility(book_id, body.visibility)
    book = get_book(book_id)
    assert book is not None
    return book


@app.post("/covers/generate")
async def post_generate_cover(
    body: CoverGenIn,
    _rl: None = Depends(rate_limit_image),
) -> Response:
    """Generate a book cover image (PNG) from title + description via Nano Banana.
    Used by the upload wizard for AI cover generation before committing the book."""
    try:
        png = await generate_cover(body.title, body.description, body.category)
    except Exception as exc:
        print(f"[covers] generation failed: {exc!r}")
        raise HTTPException(503, f"Cover generation failed: {exc}")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.delete("/books/{book_id}", status_code=204)
def remove_book(
    book_id: str,
    user: dict | None = Depends(current_user),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> Response:
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    if not _is_owner(book, user, x_client_id):
        raise HTTPException(403, "본인이 올린 책만 삭제할 수 있어요")

    # Drop the SQLite rows first; even if a file delete races we won't show
    # a half-deleted book.
    db_delete_book(book_id)
    for suffix in (".pdf", ".epub", ".thumb.png"):
        p = STORAGE / f"{book_id}{suffix}"
        try:
            p.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[delete] failed to remove {p}: {exc!r}")
    for sub in ("audio", "tts", "images"):
        d = STORAGE / sub / book_id
        if d.exists():
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception as exc:
                print(f"[delete] failed to remove {d}: {exc!r}")
    return Response(status_code=204)


@app.get("/books/{book_id}/reviews")
def get_reviews(book_id: str) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    return {"reviews": list_reviews(book_id)}


@app.post("/books/{book_id}/reviews", status_code=201)
def post_review(book_id: str, body: ReviewIn) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    return add_review(book_id, body.author, body.rating, body.text)


class ProgressIn(BaseModel):
    page: int = Field(ge=1, le=10000)


@app.put("/books/{book_id}/progress")
def put_book_progress(
    book_id: str,
    body: ProgressIn,
    identifier: str = Depends(reader_identifier),
) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    set_progress(book_id, identifier, body.page)
    return {"ok": True, "page": body.page}


@app.get("/books/{book_id}/progress")
def get_book_progress(
    book_id: str,
    identifier: str = Depends(reader_identifier),
) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    prog = get_progress(book_id, identifier)
    if not prog:
        return {"page": None, "updated_at": None}
    return prog


class HighlightIn(BaseModel):
    page: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=2000)
    note: str | None = Field(default=None, max_length=1000)
    color: str | None = Field(default=None, max_length=20)


@app.post("/books/{book_id}/highlights", status_code=201)
def post_highlight(
    book_id: str,
    body: HighlightIn,
    identifier: str = Depends(reader_identifier),
) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    return add_highlight(book_id, identifier, body.page, body.text, body.note, body.color)


@app.get("/books/{book_id}/highlights")
def get_highlights(
    book_id: str,
    identifier: str = Depends(reader_identifier),
) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    return {"highlights": list_highlights(book_id, identifier)}


@app.delete("/books/{book_id}/highlights/{highlight_id}", status_code=204)
def remove_highlight(
    book_id: str,
    highlight_id: int,
    identifier: str = Depends(reader_identifier),
) -> Response:
    ok = db_delete_highlight(highlight_id, identifier)
    if not ok:
        raise HTTPException(404, "Highlight not found or not yours")
    return Response(status_code=204)


@app.get("/books/{book_id}/summaries")
def get_summaries(book_id: str) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    return {"summaries": get_chapter_summaries(book_id)}


@app.get("/books/{book_id}/text")
def get_book_text(book_id: str) -> dict:
    """Return the full text of the book, page by page. Used by the
    novel / comfort reading modes that reflow the content."""
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    fmt = book.get("format") or "pdf"
    src = STORAGE / f"{book_id}.{fmt}"
    if not src.exists():
        raise HTTPException(404, f"{fmt.upper()} file not found on disk")
    if fmt == "epub":
        epub_data = extract_epub(src.read_bytes())
        return {"pages": epub_data["pages"]}
    return {"pages": extract_pages(src.read_bytes())}


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    page: int = Field(ge=1, default=1)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class LookupIn(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    context: str = Field(default="", max_length=1000)


LOOKUP_PROMPT = """You are a quick reference helper for a reader. The user
selected this excerpt while reading:

"{text}"

{ctx}

Reply in Korean, in 2-4 short lines (no markdown):
1. If the excerpt is a single word or short phrase → explain its meaning concisely.
2. If it's in a foreign language → give a natural Korean translation.
3. If it's a longer Korean sentence → paraphrase its meaning more plainly,
   or note any difficult/literary words within it.
4. Keep it factual; do not invent context that isn't there.

Reply only with the explanation. No intro like '이 구절은...'"""


@app.post("/books/{book_id}/lookup")
async def post_lookup(
    book_id: str,
    body: LookupIn,
    _rl: None = Depends(rate_limit_ai),
) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(500, "GEMINI_API_KEY not configured")

    ctx = f"Surrounding text for context:\n{body.context.strip()}" if body.context.strip() else ""
    prompt = LOOKUP_PROMPT.replace("{text}", body.text.strip()).replace("{ctx}", ctx)
    client = genai.Client(api_key=api_key)
    try:
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=300,
            ),
        )
    except Exception as exc:
        raise HTTPException(503, f"Lookup failed: {exc}")
    return {"result": (resp.text or "").strip()}


class DictLookupIn(BaseModel):
    word: str = Field(min_length=1, max_length=200)
    context: str = Field(default="", max_length=400)


@app.post("/dict/lookup")
async def post_dict_lookup(
    body: DictLookupIn,
    _rl: None = Depends(rate_limit_ai),
) -> dict:
    try:
        return await lookup_word(word=body.word, context=body.context or None)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    except Exception as exc:
        raise HTTPException(503, f"Dictionary lookup failed: {exc}")


@app.post("/books/{book_id}/ask")
async def post_ask(
    book_id: str,
    body: AskIn,
    _rl: None = Depends(rate_limit_ai),
) -> dict:
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    fmt = book.get("format") or "pdf"
    src = STORAGE / f"{book_id}.{fmt}"
    if not src.exists():
        raise HTTPException(404, f"{fmt.upper()} file not found")
    if fmt == "epub":
        pages = extract_epub(src.read_bytes())["pages"]
    else:
        pages = extract_pages(src.read_bytes())
    toc = get_toc(book_id) or []
    summaries = get_chapter_summaries(book_id)
    try:
        answer = await ask_book(
            title=book.get("title", ""),
            description=book.get("description"),
            toc=toc,
            summaries=summaries,
            pages=pages,
            current_page=body.page,
            history=[{"role": t.role, "content": t.content} for t in body.history],
            question=body.question,
        )
    except Exception as exc:
        print(f"[ask] failed: {exc!r}")
        raise HTTPException(503, f"AI 응답 실패: {exc}")
    return {"answer": answer or "(답변을 만들지 못했어요)"}


@app.get("/books/{book_id}/pages/{page}/tts")
async def get_page_tts(book_id: str, page: int) -> Response:
    """Stream Gemini TTS audio for a single page. Cached to disk so each
    page is generated at most once per book regardless of how many users
    re-listen to it later."""
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    if page < 1:
        raise HTTPException(400, "Page must be >= 1")

    cache_dir = STORAGE / "tts" / book_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"p{page}.wav"

    if cache_path.exists() and cache_path.stat().st_size > 44:
        return Response(
            content=cache_path.read_bytes(),
            media_type="audio/wav",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    book = get_book(book_id)
    fmt = (book or {}).get("format") or "pdf"
    src = STORAGE / f"{book_id}.{fmt}"
    if not src.exists():
        raise HTTPException(404, f"{fmt.upper()} not found on disk")
    if fmt == "epub":
        pages = extract_epub(src.read_bytes())["pages"]
    else:
        pages = extract_pages(src.read_bytes())
    if page > len(pages):
        raise HTTPException(400, f"Page {page} out of range (book has {len(pages)})")

    text = (pages[page - 1] or "").strip()
    if not text:
        from tts_generator import pcm_to_wav
        return Response(content=pcm_to_wav(b""), media_type="audio/wav")

    try:
        wav = await generate_page_tts(text)
    except Exception as exc:
        print(f"[tts] generation failed for {book_id} p{page}: {exc!r}")
        raise HTTPException(503, f"TTS generation failed: {exc}")

    if not wav:
        raise HTTPException(503, "Gemini returned empty audio")

    cache_path.write_bytes(wav)
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"Cache-Control": "private, max-age=86400"},
    )


def _chapter_window(toc: list[dict], page: int, page_count: int) -> tuple[int, str, int, int]:
    """Find the chapter that contains `page` and return
    (start_page, title, window_start, window_end) — inclusive 1-based bounds."""
    sorted_toc = sorted(toc or [], key=lambda e: int(e.get("page", 0)))
    start = 1
    title = ""
    end = page_count
    for i, entry in enumerate(sorted_toc):
        p = int(entry.get("page", 0))
        if p > page:
            end = p - 1
            break
        start = p
        title = str(entry.get("title", "")).strip()
        if i + 1 < len(sorted_toc):
            end = int(sorted_toc[i + 1].get("page", page_count + 1)) - 1
        else:
            end = page_count
    return start, title, max(1, start), min(page_count, max(start, end))


@app.api_route("/books/{book_id}/chapters/{page}/image", methods=["GET", "HEAD"])
def get_chapter_image(book_id: str, page: int) -> Response:
    """Return the cached illustration for the chapter that CONTAINS `page`.
    The cache key is the chapter's start-page, so a mid-chapter request still
    finds the right file."""
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    page_count = int(book.get("page_count", 0) or 0) or page
    toc = get_toc(book_id) or []
    start, _, _, _ = _chapter_window(toc, page, page_count)
    path = STORAGE / "images" / book_id / f"p{start}.png"
    if not path.exists():
        raise HTTPException(404, "Image not generated yet")
    return Response(
        content=path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.post("/books/{book_id}/chapters/{page}/image")
async def create_chapter_image(
    book_id: str,
    page: int,
    _rl: None = Depends(rate_limit_image),
) -> Response:
    """Generate (or regenerate) an illustration for the chapter starting at
    `page`. The result is cached so subsequent GETs are free."""
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    if page < 1:
        raise HTTPException(400, "Page must be >= 1")

    fmt = book.get("format") or "pdf"
    src = STORAGE / f"{book_id}.{fmt}"
    if not src.exists():
        raise HTTPException(404, f"{fmt.upper()} file not found")

    if fmt == "epub":
        pages = extract_epub(src.read_bytes())["pages"]
    else:
        pages = extract_pages(src.read_bytes())
    if not pages:
        raise HTTPException(400, "Book has no readable text")

    toc = get_toc(book_id) or []
    start, title, win_start, win_end = _chapter_window(toc, page, len(pages))
    summary = get_chapter_summaries(book_id).get(start, "")
    excerpt = "\n\n".join(pages[win_start - 1 : min(win_end, win_start + 2)]).strip()

    try:
        png = await generate_chapter_image(title, summary, excerpt)
    except Exception as exc:
        print(f"[chapter_image] {book_id} p{page} failed: {exc!r}")
        raise HTTPException(503, f"Image generation failed: {exc}")

    out_dir = STORAGE / "images" / book_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"p{start}.png"
    out_path.write_bytes(png)

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/books/{book_id}/images")
def list_chapter_images(book_id: str) -> dict:
    """Return the set of chapter start-pages that already have a cached image."""
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    out_dir = STORAGE / "images" / book_id
    if not out_dir.exists():
        return {"pages": []}
    pages: list[int] = []
    for p in out_dir.glob("p*.png"):
        try:
            pages.append(int(p.stem[1:]))
        except ValueError:
            continue
    return {"pages": sorted(pages)}


@app.get("/books/{book_id}/moods")
def get_book_moods(book_id: str) -> dict:
    moods = get_moods(book_id)
    if moods is None:
        raise HTTPException(404, "Book not found")
    return {"moods": moods}


@app.get("/books/{book_id}/toc")
async def get_book_toc(book_id: str) -> dict:
    if not book_exists(book_id):
        raise HTTPException(404, "Book not found")
    toc = get_toc(book_id)
    if toc is not None:
        return {"toc": toc}
    # Lazy fallback for older books that were uploaded before TOC support.
    pdf_path = STORAGE / f"{book_id}.pdf"
    pages = get_moods(book_id)  # length is page_count
    extracted: list[dict] = []
    if pdf_path.exists():
        extracted = extract_toc(pdf_path.read_bytes())
    if not extracted and pages is not None:
        # We don't have the page text in DB; re-extract from disk PDF.
        if pdf_path.exists():
            text_pages = extract_pages(pdf_path.read_bytes())
            extracted = await analyze_toc(text_pages)
    set_toc(book_id, extracted)
    return {"toc": extracted}


@app.get("/books/{book_id}/pdf")
def get_pdf(book_id: str) -> Response:
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    if (book.get("format") or "pdf") != "pdf":
        raise HTTPException(400, "This book is an EPUB. Use the text reader modes.")
    pdf_path = STORAGE / f"{book_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "PDF not found")
    return Response(
        content=pdf_path.read_bytes(),
        media_type="application/pdf",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/books/{book_id}/cover", status_code=200)
async def replace_cover(
    book_id: str,
    cover: UploadFile,
    _owner: dict = Depends(require_book_owner),
) -> dict:
    cover_png = await _read_cover(cover)
    if cover_png is None:
        raise HTTPException(400, "No cover image provided")
    (STORAGE / f"{book_id}.thumb.png").write_bytes(cover_png)
    return {"ok": True, "bytes": len(cover_png)}


@app.get("/books/{book_id}/thumb")
def get_thumb(book_id: str) -> Response:
    thumb_path = STORAGE / f"{book_id}.thumb.png"
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return Response(
        content=thumb_path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.websocket("/ws/music/{book_id}")
async def music_ws(ws: WebSocket, book_id: str) -> None:
    # The HTTP middleware doesn't run for WebSockets, so the same access
    # check has to live here. Browsers can't set custom headers on the
    # WebSocket handshake, so we read identity from query params:
    #   ?token=<google-id-token>   (preferred)
    #   ?cid=<anon-client-id>      (fallback for not-signed-in users)
    if not is_valid_book_id(book_id):
        await ws.close(code=4404)
        return
    book = get_book(book_id)
    if not book:
        await ws.close(code=4404)
        return
    qp = ws.query_params
    token = qp.get("token")
    user = verify_google_token_cached(token) if token else None
    client_id = qp.get("cid")
    if not book_visible_to(book, user, client_id):
        # 4403 = custom close code for "access denied"
        await ws.close(code=4403)
        return
    moods = get_moods(book_id)
    if not moods:
        await ws.close(code=4404)
        return
    await ws.accept()
    session = MusicSession(book_id, moods)
    try:
        await session.run(ws)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"[main] music_ws error: {exc!r}")
        message = f"{type(exc).__name__}: {exc}"
        try:
            await ws.send_text(json.dumps({"type": "error", "message": message}))
        except Exception:
            pass
        try:
            await ws.close(code=1011)
        except Exception:
            pass
