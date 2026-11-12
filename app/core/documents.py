import io
import shutil
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.core.config import settings


class DocumentExtractionError(RuntimeError):
    pass


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return "\n".join(paragraph.text for paragraph in Document(str(path)).paragraphs)
    if suffix in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError("Supported formats: PDF, DOCX, TXT, MD, and CSV.")


def _extract_pdf(path: Path) -> str:
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages).strip()
    if len(text) >= settings.ocr_min_text_chars or not settings.ocr_enabled:
        return text
    ocr_text = _ocr_pdf(path).strip()
    if ocr_text:
        return ocr_text
    if text:
        return text
    raise DocumentExtractionError(
        "The PDF contains no extractable text and OCR returned no text. "
        "Check OCR_PROVIDER, Tesseract/Textract configuration, and scan quality."
    )


def _ocr_pdf(path: Path) -> str:
    try:
        import fitz
    except ImportError as exc:
        raise DocumentExtractionError(
            "Scanned-PDF OCR requires PyMuPDF. Reinstall the project dependencies."
        ) from exc

    document = fitz.open(path)
    pages = min(len(document), settings.ocr_max_pages)
    if not pages:
        return ""
    output: list[str] = []
    zoom = settings.ocr_dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    provider = settings.ocr_provider.lower()
    try:
        for page_number in range(pages):
            pixmap = document[page_number].get_pixmap(matrix=matrix, alpha=False)
            png_bytes = pixmap.tobytes("png")
            if provider == "textract":
                page_text = _ocr_image_textract(png_bytes)
            elif provider == "local":
                page_text = _ocr_image_local(png_bytes)
            else:
                raise DocumentExtractionError(
                    "OCR_PROVIDER must be either 'local' or 'textract'."
                )
            if page_text.strip():
                output.append(f"[Page {page_number + 1}]\n{page_text.strip()}")
    finally:
        document.close()
    return "\n\n".join(output)


def _ocr_image_local(image_bytes: bytes) -> str:
    if shutil.which("tesseract") is None:
        raise DocumentExtractionError(
            "This scanned PDF needs OCR, but the Tesseract binary is not installed. "
            "On macOS run 'brew install tesseract', or set OCR_PROVIDER=textract on AWS."
        )
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise DocumentExtractionError(
            "Local OCR requires pytesseract and Pillow. Reinstall the project dependencies."
        ) from exc
    with Image.open(io.BytesIO(image_bytes)) as image:
        return pytesseract.image_to_string(image, lang=settings.ocr_language)


def _ocr_image_textract(image_bytes: bytes) -> str:
    try:
        import boto3
    except ImportError as exc:
        raise DocumentExtractionError("AWS Textract OCR requires boto3.") from exc
    response = boto3.client("textract", region_name=settings.aws_region).detect_document_text(
        Document={"Bytes": image_bytes}
    )
    return "\n".join(
        block.get("Text", "")
        for block in response.get("Blocks", [])
        if block.get("BlockType") == "LINE"
    )
