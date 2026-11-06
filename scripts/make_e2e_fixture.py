"""Generate the small PDF used by the Playwright teacher flow.

Run once from the repository root:
    backend/.venv/Scripts/python.exe scripts/make_e2e_fixture.py

The fixture is committed so e2e runs do not need reportlab.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

TARGET = Path(__file__).resolve().parents[1] / "web" / "e2e" / "fixtures" / "demo.pdf"

PAGES: list[tuple[str, list[str]]] = [
    (
        "Photosynthesis",
        [
            "Photosynthesis is the process by which green plants use sunlight to make food.",
            "Chlorophyll in the chloroplast absorbs light energy from the sun.",
            "Carbon dioxide and water are converted into glucose and oxygen.",
            "The overall reaction is six carbon dioxide plus six water yielding glucose and six oxygen.",
        ],
    ),
    (
        "Respiration",
        [
            "Cellular respiration releases energy stored in glucose.",
            "It takes place in the mitochondria of plant and animal cells.",
            "Aerobic respiration uses oxygen and produces carbon dioxide and water.",
            "Fermentation is an anaerobic pathway that produces less energy.",
        ],
    ),
]


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(TARGET), pagesize=A4)
    width, height = A4
    for title, lines in PAGES:
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(72, height - 90, title)
        pdf.setFont("Helvetica", 12)
        y = height - 130
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 22
        pdf.showPage()
    pdf.save()
    print(TARGET)


if __name__ == "__main__":
    main()
