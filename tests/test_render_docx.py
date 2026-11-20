"""The Word renderer for a resume must cope with a resume that only has what it needs (a top-level `basics`): a pasted or hand-edited resume often has no
title, no summary, or no skills, and every download button used to fail with a KeyError. Uses the REAL renderer (the suite's other tests replace it with a stand-in)."""
import io
from pathlib import Path

import pytest
import yaml
from docx import Document

from app import render

real_build_docx = render.build_docx  # taken at import, before the suite's fixtures swap in the stand-in
EXAMPLE = Path(__file__).resolve().parent.parent / "example-resume.yaml"


def text_of(data, tmp_path):
    real_build_docx(data, tmp_path)
    return "\n".join(p.text for p in Document(str(tmp_path / "resume.docx")).paragraphs)


@pytest.mark.parametrize("data", [
    {"basics": {"name": "Bob"}},
    {"basics": {"name": "Bob"}, "experience": [], "education": [], "skills": []},
    {"basics": {"name": "Bob"}, "experience": None, "education": None, "skills": None},
    {"basics": {}},
])
def test_a_resume_with_only_a_basics_key_renders(data, tmp_path):
    assert text_of(data, tmp_path) is not None


def test_missing_optional_fields_are_left_out_not_fatal(tmp_path):
    data = {"basics": {"name": "Bob Builder", "email": "bob@example.com"},
            "experience": [{"company": "Acme", "bullets": ["Built things"]}],   # no title, dates or location
            "skills": [{"items": ["Python", "Go"]}],                          # no category
            "certifications": [{"issuer": "Org"}], "projects": [{"description": "A thing"}]}
    text = text_of(data, tmp_path)
    assert "Bob Builder" in text and "bob@example.com" in text and "Acme" in text and "Built things" in text and "Python, Go" in text


def test_empty_sections_get_no_heading(tmp_path):
    text = text_of({"basics": {"name": "Bob"}}, tmp_path)
    assert not any(word in text.lower() for word in ("experience", "education", "skills"))
    text = text_of({"basics": {"name": "Bob"}, "experience": [{"company": "Hidden", "title": "X", "include_in_resume": False}]}, tmp_path)
    assert "Hidden" not in text and "experience" not in text.lower()  # a job kept off the printed resume leaves no empty section either


def test_the_full_example_resume_still_renders_everything(tmp_path):
    data = yaml.safe_load(EXAMPLE.read_text())
    text = text_of(data, tmp_path).lower()  # headings are printed in capitals
    for expected in ("Jordan Rivera", "Senior Software Engineer", "Experience", "Education", "Skills", "Meridian Health"):
        assert expected.lower() in text, expected


def test_the_web_page_leaves_out_an_empty_role_line():
    html = render.render_html({"basics": {"name": "Bob"}}, show_download_bar=False)
    assert "Bob" in html and 'class="role"' not in html
    assert 'class="role"' in render.render_html({"basics": {"name": "Bob", "title": "Engineer"}}, show_download_bar=False)
