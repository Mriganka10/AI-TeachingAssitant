import mimetypes
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm import llm
from app.agents.prompts import RESEARCH_SYSTEM, TEACHING_SYSTEM
from app.agents.retrieval import retrieve_context
from app.artifacts.generator import create_artifacts
from app.core.audit import audit
from app.core.auth import CurrentUser, auth_service, current_user
from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.documents import extract_text
from app.core.job_queue import enqueue_agent_job
from app.core.models import AgentJob, Artifact, SourceDocument
from app.core.storage import checksum, storage
from app.schemas import OTPRequest, OTPVerify, ResearchRequest, TeachingRequest

router = APIRouter(prefix="/api")


@router.post("/auth/request-otp")
def request_otp(payload: OTPRequest, request: Request, db: Session = Depends(get_db)):
    result = auth_service.request_otp(db, payload.email)
    audit(
        db,
        tenant_id=result.email.split("@")[-1],
        actor_email=result.email,
        event_type="auth.email_verification_requested"
        if result.status == "verification_required"
        else "auth.otp_requested",
        status="success",
        request=request,
        details={"delivery": result.delivery, "status": result.status},
    )
    response = {
        "message": result.message,
        "email": result.email,
        "status": result.status,
        "delivery": result.delivery,
    }
    if result.dev_otp:
        response["dev_otp"] = result.dev_otp
    return response


@router.post("/auth/register-email")
def register_email(payload: OTPRequest, request: Request, db: Session = Depends(get_db)):
    result = auth_service.register_email(db, payload.email)
    audit(
        db,
        tenant_id=result.email.split("@")[-1],
        actor_email=result.email,
        event_type="auth.email_verification_requested",
        status="success",
        request=request,
        details={"delivery": result.delivery, "status": result.status},
    )
    return {
        "message": result.message,
        "email": result.email,
        "status": result.status,
        "delivery": result.delivery,
    }


@router.post("/auth/verify")
def verify_otp(payload: OTPVerify, response: Response, request: Request, db: Session = Depends(get_db)):
    user, token = auth_service.verify_otp(db, payload.email, payload.otp)
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_minutes * 60,
    )
    audit(
        db,
        tenant_id=user.tenant_id,
        actor_email=user.email,
        event_type="auth.login",
        status="success",
        request=request,
    )
    return {"email": user.email, "role": user.role}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth_service.logout(db, request.cookies.get(settings.cookie_name))
    response.delete_cookie(settings.cookie_name)
    return {"message": "Signed out."}


@router.get("/auth/me")
def me(user: CurrentUser = Depends(current_user)):
    return {"email": user.email, "role": user.role, "tenant_id": user.tenant_id}


@router.post("/documents")
async def upload_document(
    request: Request,
    collection: str = Form(...),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    size_limit = settings.max_upload_mb * 1024 * 1024
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md", ".csv"}:
        raise HTTPException(status_code=415, detail="Unsupported document format.")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
        content = await file.read(size_limit + 1)
        if len(content) > size_limit:
            raise HTTPException(status_code=413, detail="File exceeds upload limit.")
        temp.write(content)
        temp_path = Path(temp.name)
    try:
        text = extract_text(temp_path)
        document = SourceDocument(
            tenant_id=user.tenant_id,
            uploaded_by=user.email,
            collection=collection,
            filename=Path(file.filename or "document").name,
            content_type=file.content_type,
            size_bytes=len(content),
            checksum_sha256=checksum(temp_path),
            storage_uri="pending",
            extracted_text=text,
        )
        db.add(document)
        db.flush()
        document.storage_uri = storage.save(
            temp_path, tenant_id=user.tenant_id, category="rag", object_id=document.id
        )
        db.commit()
        audit(
            db,
            tenant_id=user.tenant_id,
            actor_email=user.email,
            event_type="document.uploaded",
            status="success",
            request=request,
            entity_type="document",
            entity_id=document.id,
            details={"filename": document.filename, "collection": collection},
        )
        return {"id": document.id, "filename": document.filename, "collection": collection}
    finally:
        temp_path.unlink(missing_ok=True)


@router.get("/documents")
def list_documents(
    user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)
):
    docs = db.scalars(
        select(SourceDocument)
        .where(SourceDocument.tenant_id == user.tenant_id)
        .order_by(SourceDocument.created_at.desc())
    )
    return [
        {
            "id": doc.id,
            "filename": doc.filename,
            "collection": doc.collection,
            "size_bytes": doc.size_bytes,
            "status": doc.status,
        }
        for doc in docs
    ]


@router.post("/agents/teaching")
def teaching_agent(
    payload: TeachingRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    return _enqueue_agent("teaching", payload.model_dump(), background_tasks, request, user, db)


@router.post("/agents/research")
def research_agent(
    payload: ResearchRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    return _enqueue_agent("research", payload.model_dump(), background_tasks, request, user, db)


def _enqueue_agent(
    agent_type: str,
    payload: dict,
    background_tasks: BackgroundTasks,
    request: Request,
    user: CurrentUser,
    db: Session,
):
    job = AgentJob(
        tenant_id=user.tenant_id,
        created_by=user.email,
        agent_type=agent_type,
        request_payload=payload,
    )
    db.add(job)
    db.commit()
    audit(
        db,
        tenant_id=user.tenant_id,
        actor_email=user.email,
        event_type=f"agent.{agent_type}.queued",
        status="success",
        request=request,
        entity_type="job",
        entity_id=job.id,
        details={"async": True},
    )
    if settings.uses_sqs_agent_queue:
        try:
            enqueue_agent_job(job.id)
        except Exception as exc:
            job.status = "failed"
            job.error_message = "The agent queue is temporarily unavailable."
            job.completed_at = datetime.now(UTC)
            db.commit()
            raise HTTPException(status_code=503, detail=job.error_message) from exc
    else:
        background_tasks.add_task(run_agent_job, job.id)
    return _serialize_job(db, job, include_result=False)


def run_agent_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    try:
        job = db.get(AgentJob, job_id)
        if not job:
            return
        if job.status == "completed":
            return
        agent_type = job.agent_type
        payload = job.request_payload
        tenant_id = job.tenant_id
        actor_email = job.created_by
        system_prompt = TEACHING_SYSTEM if agent_type == "teaching" else RESEARCH_SYSTEM
        query = payload.get("topic") or payload.get("research_topic", "")
        contexts = retrieve_context(
            db,
            tenant_id=tenant_id,
            query=query,
            collections=payload.get("collections", []),
        )
        model_payload = {**payload, "uploaded_context": contexts}
        result, model = llm.generate(
            system=system_prompt,
            payload=model_payload,
            use_web_search=payload.get("use_web_search", False),
        )
        paths = create_artifacts(
            result,
            agent_type=agent_type,
            output_dir=settings.data_dir / "generated" / job.id,
        )
        artifacts = []
        for path in paths:
            artifact = Artifact(
                tenant_id=tenant_id,
                job_id=job.id,
                artifact_type=path.suffix.lstrip("."),
                filename=path.name,
                content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                storage_uri="pending",
                size_bytes=path.stat().st_size,
                checksum_sha256=checksum(path),
            )
            db.add(artifact)
            db.flush()
            artifact.storage_uri = storage.save(
                path, tenant_id=tenant_id, category="artifacts", object_id=artifact.id
            )
            artifacts.append(artifact)
        job.status = "completed"
        job.result_payload = result
        job.model = model
        job.completed_at = datetime.now(UTC)
        db.commit()
        audit(
            db,
            tenant_id=tenant_id,
            actor_email=actor_email,
            event_type=f"agent.{agent_type}.completed",
            status="success",
            entity_type="job",
            entity_id=job.id,
            details={"model": model, "artifact_count": len(artifacts)},
        )
    except Exception as exc:
        db.rollback()
        job = db.get(AgentJob, job_id)
        if not job:
            return
        job.status = "failed"
        job.error_message = str(exc)[:2000]
        job.completed_at = datetime.now(UTC)
        db.commit()
        audit(
            db,
            tenant_id=tenant_id,
            actor_email=job.created_by,
            event_type=f"agent.{job.agent_type}.failed",
            status="failed",
            entity_type="job",
            entity_id=job.id,
            details={"error": str(exc)[:500]},
        )
    finally:
        db.close()


def _serialize_job(db: Session, job: AgentJob, *, include_result: bool = True) -> dict:
    artifacts = []
    if job.status == "completed":
        artifacts = db.scalars(
            select(Artifact)
            .where(Artifact.job_id == job.id, Artifact.tenant_id == job.tenant_id)
            .order_by(Artifact.created_at.asc())
        ).all()
    response = {
        "job_id": job.id,
        "status": job.status,
        "result": job.result_payload if include_result and job.status == "completed" else None,
        "error": job.error_message if job.status == "failed" else None,
        "artifacts": [
            {"id": item.id, "filename": item.filename, "type": item.artifact_type}
            for item in artifacts
        ],
    }
    return response


@router.get("/artifacts/{artifact_id}")
def download_artifact(
    artifact_id: str,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.tenant_id == user.tenant_id,
        )
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    path = storage.materialize(artifact.storage_uri)
    return FileResponse(path, filename=artifact.filename, media_type=artifact.content_type)


@router.get("/jobs")
def list_jobs(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    jobs = db.scalars(
        select(AgentJob)
        .where(AgentJob.tenant_id == user.tenant_id)
        .order_by(AgentJob.started_at.desc())
        .limit(50)
    )
    return [
        {
            "id": job.id,
            "agent_type": job.agent_type,
            "status": job.status,
            "model": job.model,
            "title": (
                job.request_payload.get("topic")
                or job.request_payload.get("research_topic")
                or "Untitled faculty task"
            ),
            "course": job.request_payload.get("course") or job.request_payload.get("discipline"),
            "started_at": job.started_at,
        }
        for job in jobs
    ]


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    job = db.scalar(
        select(AgentJob).where(
            AgentJob.id == job_id,
            AgentJob.tenant_id == user.tenant_id,
        )
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _serialize_job(db, job)
