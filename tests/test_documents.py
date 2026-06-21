from pathlib import Path

from reportlab.pdfgen import canvas

from app.core import documents


def test_scanned_pdf_uses_ocr_fallback(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "scan.pdf"
    output = canvas.Canvas(str(pdf_path))
    output.rect(20, 20, 200, 100)
    output.save()

    monkeypatch.setattr(documents.settings, "ocr_enabled", True)
    monkeypatch.setattr(documents.settings, "ocr_min_text_chars", 80)
    monkeypatch.setattr(documents, "_ocr_pdf", lambda _: "OCR extracted lecture notes")

    assert documents.extract_text(pdf_path) == "OCR extracted lecture notes"
