"""Small generated PDFs for extraction and ingestion tests."""

from __future__ import annotations

import textwrap
from io import BytesIO

from reportlab.lib import pdfencrypt
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

BODY = (
    "Chemical reactions rearrange atoms without creating or destroying matter. "
    "Reactants become products, and the total mass is conserved. "
    "Catalysts change the rate of a reaction without being consumed. "
    "Energy is absorbed or released when chemical bonds break and form. "
)


def make_pdf_bytes(*, pages: int = 1, heading: str = "Chemical Reactions") -> bytes:
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=LETTER)
    for index in range(pages):
        document.setFont("Helvetica-Bold", 18)
        document.drawString(72, 720, f"{heading} {index + 1}")
        document.setFont("Helvetica", 10)
        text = document.beginText(72, 690)
        text.setFont("Helvetica", 10)
        for line in textwrap.wrap(BODY * 3, width=90)[:18]:
            text.textLine(line)
        document.drawText(text)
        document.showPage()
    document.save()
    return buffer.getvalue()


def make_blank_pdf_bytes(*, pages: int = 1) -> bytes:
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=LETTER)
    for _ in range(pages):
        document.showPage()
    document.save()
    return buffer.getvalue()


def make_encrypted_pdf_bytes(*, password: str = "secret") -> bytes:
    buffer = BytesIO()
    encryption = pdfencrypt.StandardEncryption(password)
    document = canvas.Canvas(buffer, pagesize=LETTER, encrypt=encryption)
    document.setFont("Helvetica", 12)
    document.drawString(72, 720, "This content is encrypted")
    document.showPage()
    document.save()
    return buffer.getvalue()
