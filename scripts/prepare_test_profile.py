"""A template with deliberate foreign personal text; final DOCX must not copy it."""

import argparse
from pathlib import Path

from docx import Document

from biography.templates import analyze_samples, create_profile

p = argparse.ArgumentParser()
p.add_argument("--data-directory", type=Path, required=True)
a = p.parse_args()
a.data_directory.mkdir(parents=True, exist_ok=True)
source = a.data_directory / "source-template.docx"
d = Document()
d.add_heading("SECRET_TEMPLATE_PERSON", 0)
t = d.add_table(rows=2, cols=2)
t.cell(0, 0).text = "SECRET_TEMPLATE_PERSON"
t.cell(1, 1).text = "Составлено по интервью"
d.save(source)
blocks = analyze_samples([source])
choices = [{"field": "full_name", "fixed": False} for _ in blocks]
choices[-1] = {"field": "review_notice", "fixed": True, "fixed_confirmed": True}
create_profile(
    [source], choices, a.data_directory / "test-profile", "Проверочный профиль"
)
