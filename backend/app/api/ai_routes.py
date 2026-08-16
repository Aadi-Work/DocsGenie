from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends

from app.api.auth_routes import get_current_user
from app.models.schemas import AiAnswerRequest, AiGenerateRequest, AiStartRequest, ChatRequest
from app.services.auth_service import AuthUser
from app.services.bedrock_service import get_bedrock
from app.services.document_service import get_documents
from app.services.session_store import GenerationSession, get_sessions, new_session_id
from app.services.template_service import get_templates
from app.utils.file_utils import AppError, dedupe_answers, download_url, preview_url

router = APIRouter(prefix="/api", tags=["ai"])

GREETING_RE = re.compile(
    r"^(hi|hello|hey|yo|thanks|thank you|good (morning|afternoon|evening)|how are you)[\s!.?]*$",
    re.I,
)


def _session_public(session: GenerationSession, reply: str) -> dict:
    tmpl = None
    if session.template_id:
        try:
            tmpl = get_templates().get(session.template_id)
        except AppError:
            tmpl = {
                "id": session.template_id,
                "name": session.template_name,
                "s3_key": session.template_key,
                "original_filename": session.template_key.rsplit("/", 1)[-1] if session.template_key else None,
                "output_format": session.template_type,
                "versions": [],
                "placeholders": [],
                "context_questions": [],
                "content_outline": [],
                "tags": [],
                "category": session.template_type,
                "description": "",
                "usage_count": 0,
            }
    missing = [str(f.get("label") or f.get("id")) for f in session.missing_fields()]
    current = session.current_field()
    download = None
    if session.generated_filename and session.generated_key:
        download = download_url(session.generated_filename, session.generated_key)
    return {
        "session_id": session.session_id,
        "reply": reply,
        "stage": session.generation_status,
        "template": tmpl,
        "answers": dedupe_answers(session.answers),
        "questions": [str(f.get("question") or f.get("label")) for f in session.required_fields],
        "current_question": (current or {}).get("question") if current else None,
        "missing_fields": missing,
        "download_url": download,
        "preview_url": preview_url(session.generated_filename, session.generated_key) if session.generated_key else None,
        "template_preview_filename": f"blank_{session.template_id}.{session.template_type}" if session.template_id else None,
        "generated_filename": session.generated_filename,
        "s3_key": session.generated_key,
        "messages": session.messages,
        "question": (current or {}).get("question") if current else None,
        "template_name": session.template_name,
        "generation_status": session.generation_status,
    }


def _begin(session: GenerationSession, tmpl: dict, analysis: dict) -> str:
    session.template_id = tmpl["id"]
    session.template_key = tmpl["s3_key"]
    session.template_name = tmpl["name"]
    session.template_type = str(tmpl.get("output_format") or tmpl.get("type") or "")
    session.template_version = str(tmpl.get("current_version") or "")
    session.required_fields = list(tmpl.get("field_config") or analysis.get("field_config") or [])
    session.pending_questions = [str(f.get("question") or f.get("label")) for f in session.required_fields]
    session.generation_status = "clarifying"
    session.field_index = 0
    profile_id = str(tmpl.get("profile_id") or "")
    if profile_id:
        try:
            from app.office.profiles import empty_form

            for key, val in empty_form(profile_id).items():
                text = str(val or "").strip()
                if text and not str(session.answers.get(key) or "").strip():
                    session.answers[key] = text
                    session.collected_fields[key] = text
        except Exception:
            pass
    first = session.current_field()
    question = str((first or {}).get("question") or "What details should go into this document?")
    total = len([f for f in session.required_fields if f.get("required", True)])
    return (
        f"I selected **{tmpl['name']}** from S3 (`{tmpl.get('s3_key')}`).\n\n"
        f"Paste loose notes, attach a file, or answer the questions one at a time.\n\n"
        f"**1/{total}. {question}**"
    )


def _visible_user_message(req: ChatRequest) -> str:
    message = (req.message or "").strip()
    name = (req.attachment_name or "").strip()
    if name and message:
        return f"{message}\n\nAttached {name}"
    if name:
        return f"Attached {name}"
    return message


def _notes_blob(req: ChatRequest) -> str:
    return "\n\n".join(p for p in [(req.message or "").strip(), (req.attachment_text or "").strip()] if p)


@router.post("/ai/start")
def ai_start(req: AiStartRequest, user: AuthUser = Depends(get_current_user)):
    try:
        tmpl = get_templates().get(req.template_key) if "/" not in req.template_key else get_templates().resolve(req.template_key)
        tmpl = get_templates().get(tmpl["id"])
        analysis = get_templates().analyze_template(tmpl["id"]) if not tmpl.get("field_config") else {}
    except AppError as exc:
        raise exc.http() from exc
    session = GenerationSession(session_id=new_session_id(), username=user.email)
    reply = _begin(session, tmpl, analysis)
    session.messages.append({"role": "assistant", "content": reply})
    get_sessions().save(session)
    return {
        "session_id": session.session_id,
        "template": tmpl.get("original_filename") or tmpl["name"],
        "question": session.current_field()["question"] if session.current_field() else None,
        **_session_public(session, reply),
    }


@router.post("/ai/answer")
def ai_answer(req: AiAnswerRequest, user: AuthUser = Depends(get_current_user)):
    session = get_sessions().get(req.session_id)
    if not session or session.username != user.email:
        raise AppError(404, "Session not found").http()
    reply = _apply_answer(session, req.answer)
    get_sessions().save(session)
    return _session_public(session, reply)


@router.post("/ai/generate")
def ai_generate(req: AiGenerateRequest, user: AuthUser = Depends(get_current_user)):
    session = get_sessions().get(req.session_id)
    if not session or session.username != user.email:
        raise AppError(404, "Session not found").http()
    if req.answers:
        session.answers.update({k: str(v) for k, v in req.answers.items() if v})
    try:
        result = get_documents().generate(
            template_key=session.template_key,
            answers=session.answers,
            generated_by=user.email,
            template_id=session.template_id,
            template_name=session.template_name,
            template_version=session.template_version,
        )
    except AppError as exc:
        raise exc.http() from exc
    session.generation_status = "ready"
    session.generated_key = result["s3_key"]
    session.generated_filename = result["filename"]
    get_sessions().save(session)
    return {
        "success": True,
        "document_name": result["filename"],
        "s3_key": result["s3_key"],
        "download_url": download_url(result["filename"], result["s3_key"]),
        "preview_url": preview_url(result["filename"], result["s3_key"]),
        **_session_public(session, f"**{session.template_name}** is filled from S3. Preview it in the app, then download if you want the file."),
    }


@router.post("/chat")
def chat(req: ChatRequest, user: AuthUser = Depends(get_current_user)):
    store = get_sessions()
    session = store.get(req.session_id) if req.session_id else None
    if session is None:
        session = GenerationSession(session_id=new_session_id(), username=user.email)
    message = (req.message or "").strip()
    notes = _notes_blob(req)
    visible = _visible_user_message(req)
    template_id = (req.template_id or "").strip()
    try:
        if template_id and (not session.template_id or session.template_id != template_id or session.generation_status in {"idle", "ready"}):
            tmpl = get_templates().get(template_id)
            analysis = {} if tmpl.get("field_config") else get_templates().analyze_template(tmpl["id"])
            if visible:
                session.messages.append({"role": "user", "content": visible})
            reply = _start_with_notes(session, tmpl, analysis, notes)
        elif session.generation_status == "clarifying" and session.template_key:
            if not notes:
                raise AppError(400, "Send a reply, paste notes, or attach a file.")
            if visible:
                session.messages.append({"role": "user", "content": visible})
            reply = _apply_answer(session, notes)
        elif GREETING_RE.match(message or "hi") and not (req.attachment_text or "").strip():
            session.generation_status = "idle"
            reply = (
                "Hi — choose a template on the left, or tell me what document you need, "
                "for example meeting minutes or a BRD."
            )
        else:
            tmpl = _select_template(notes or message)
            if not tmpl:
                session.generation_status = "idle"
                reply = (
                    "I don't have a matching Office template in S3 for that. "
                    "Pick a template on the left, or ask for meeting minutes, a POC list, or a BRD."
                )
            else:
                tmpl = get_templates().get(tmpl["id"])
                analysis = {} if tmpl.get("field_config") else get_templates().analyze_template(tmpl["id"])
                if visible:
                    session.messages.append({"role": "user", "content": visible})
                reply = _start_with_notes(session, tmpl, analysis, notes)
        session.messages.append({"role": "assistant", "content": reply})
        store.save(session)
        return _session_public(session, reply)
    except AppError as exc:
        raise exc.http() from exc


def _looks_like_notes(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return len(raw) > 40 or "\n" in raw or raw.count(".") >= 2 or raw.count(":") >= 2 or raw.count("-") >= 3


def _ingest_notes(session: GenerationSession, tmpl: dict, notes: str) -> dict[str, str]:
    text = (notes or "").strip()
    if not text:
        return {}
    filled: dict[str, str] = {}
    fields = list(session.required_fields or tmpl.get("field_config") or [])
    profile_id = str(tmpl.get("profile_id") or "")
    if profile_id:
        try:
            from app.office.profiles import extract_form

            extracted = extract_form(profile_id, text)
            for key, value in (extracted.get("filled_fields") or {}).items():
                if str(value or "").strip():
                    filled[str(key)] = str(value).strip()
        except Exception:
            filled.update(_extract_from_message(text, fields))
    required = [f for f in fields if f.get("required", True)]
    got = sum(
        1
        for f in required
        if str(filled.get(str(f.get("id") or "")) or filled.get(str(f.get("label") or "")) or "").strip()
    )
    if not profile_id or (required and got < max(1, len(required) // 3)):
        extra = _extract_from_message(text, fields)
        for key, value in extra.items():
            if value and not str(filled.get(key) or "").strip():
                filled[key] = value
    for field in fields:
        ident = str(field.get("id") or "")
        label = str(field.get("label") or "")
        value = str(filled.get(ident) or filled.get(label) or "").strip()
        if not value:
            continue
        if ident:
            filled[ident] = value
        if label:
            filled[label] = value
    session.answers.update(filled)
    session.collected_fields.update(filled)
    return filled


def _progress_question(session: GenerationSession, prefix: str = "") -> str:
    missing = session.missing_fields()
    if not missing:
        return _finalize(session)
    nxt = missing[0]
    total = len([f for f in session.required_fields if f.get("required", True)])
    answered = max(0, total - len(missing))
    session.generation_status = "clarifying"
    body = f"**{answered + 1}/{total}. {nxt.get('question') or nxt.get('label')}**"
    return f"{prefix}{body}" if prefix else body


def _start_with_notes(session: GenerationSession, tmpl: dict, analysis: dict, notes: str) -> str:
    intro = _begin(session, tmpl, analysis)
    if not notes.strip():
        return intro
    filled = _ingest_notes(session, tmpl, notes)
    if not session.missing_fields():
        return _finalize(session)
    got = len([v for v in filled.values() if str(v).strip()])
    prefix = (
        f"I selected **{tmpl['name']}**.\n\n"
        f"I pulled {got} field{'s' if got != 1 else ''} from your notes"
        f"{' / attached file' if got else ''}. "
        f"I'll only ask for what's still missing.\n\n"
        if got
        else f"I selected **{tmpl['name']}**.\n\nI couldn't map those notes yet — let's go question by question.\n\n"
    )
    return _progress_question(session, prefix)


def _apply_answer(session: GenerationSession, answer: str) -> str:
    tmpl = {}
    if session.template_id:
        try:
            tmpl = get_templates().get(session.template_id)
        except AppError:
            tmpl = {}
    ingested = False
    if _looks_like_notes(answer) and tmpl:
        _ingest_notes(session, tmpl, answer)
        ingested = True
    current = session.current_field()
    if current:
        ident = str(current.get("id") or current.get("label"))
        if not str(session.answers.get(ident) or "").strip():
            # Do not dump a whole attached file / notes blob into the next empty field.
            if not (ingested and len(answer.strip()) > 200):
                session.answers[ident] = answer.strip()
                session.collected_fields[ident] = answer.strip()
    return _progress_question(session, "Thanks.\n\n")


def _finalize(session: GenerationSession) -> str:
    result = get_documents().generate(
        template_key=session.template_key,
        answers=session.answers,
        generated_by=session.username,
        template_id=session.template_id,
        template_name=session.template_name,
        template_version=session.template_version,
    )
    session.generation_status = "ready"
    session.generated_key = result["s3_key"]
    session.generated_filename = result["filename"]
    return (
        f"**{session.template_name}** is filled. Preview the document in the app first, "
        "then download it if you want to save the file."
    )


def _select_template(message: str) -> dict | None:
    templates = get_templates().list_templates()
    if not templates:
        return None
    intent = get_bedrock().json_invoke(
        json.dumps(
            {
                "message": message,
                "templates": [
                    {
                        "id": t["id"],
                        "name": t["name"],
                        "filename": t.get("original_filename"),
                        "type": t.get("type"),
                        "key": t.get("s3_key"),
                    }
                    for t in templates
                ],
            }
        ),
        system=(
            "You select an Office template stored in AWS S3. "
            "Greetings and unrelated questions are not template requests. "
            "Only match when the user wants a document this hub can produce. "
            'Return JSON {"is_template_request":true,"matched":true,"template_id":"...","reason":"..."}.'
        ),
    )
    if intent.get("is_template_request") is False or intent.get("matched") is False:
        return None
    ident = str(intent.get("template_id") or "").strip()
    return next((t for t in templates if t["id"] == ident or t.get("s3_key") == ident or t.get("name") == ident), None)


def _extract_from_message(message: str, fields: list[dict]) -> dict[str, str]:
    if not fields:
        return {}
    data = get_bedrock().json_invoke(
        json.dumps({"message": message, "fields": fields}),
        system=(
            "Extract any answers already present in the user message for these template fields. "
            "The message may be unstructured notes, bullets, an email, or a single answer. "
            "Map meaning, not only matching labels. "
            'Return JSON {"answers":{"id":"value"}}. Use empty string if unknown. Do not invent facts.'
        ),
    )
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    out: dict[str, str] = {}
    for field in fields:
        ident = str(field.get("id") or "")
        label = str(field.get("label") or ident)
        value = str(answers.get(ident) or answers.get(label) or "").strip()
        if value:
            out[ident] = value
    return out
