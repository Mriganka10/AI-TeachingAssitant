"""Publication-grade revision: verified sources, plan, writing checks and assembly."""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document

from app.agents import revision as revision_module
from app.agents import sources
from app.agents.llm import LLMService
from app.agents.manuscript import Manuscript, Section, parse_manuscript
from app.agents.revision import build_revision, label_headings, normalize_plan
from app.artifacts.generator import create_artifacts
from app.core.config import settings

META_DOI = "10.3102/0034654316689306"
REGISTRY = {
    META_DOI: {
        "title": "Rethinking the Use of Tests: A Meta-Analysis of Practice Testing",
        "authors": ["Olusola O. Adesope", "Dominic A. Trevisan", "Narayankripa Sundararajan"],
        "year": "2017", "venue": "Review of Educational Research", "volume": "87", "issue": "3",
        "pages": "659-701", "doi": META_DOI, "url": f"https://doi.org/{META_DOI}",
    },
}

NUMERIC_PAPER = """Spaced Quizzes in Large Statistics Courses
A. Sen and R. Das, Department of Statistics

Abstract
Retrieval practice is widely recommended, yet its classroom effect in statistics is unclear. We studied 214 students.

1. Introduction
Retrieving information strengthens recall [1]. Spacing practice adds a further benefit [2]. Few studies examine quantitative courses where students solve problems rather than recall facts.

2. Methods
Participants were 214 undergraduates in two sections. The spaced group completed 12 weekly quizzes with 10 questions each. Scores were compared with a t-test.

3. Results
The spaced group scored 71.2 and the massed group 62.8, a difference of 8.4 points (p = 0.003).

References
[1] H. L. Roediger and J. D. Karpicke, "Test-enhanced learning," Psychological Science, vol. 17, no. 3, pp. 249-255, 2006.
[2] N. J. Cepeda et al., "Distributed practice in verbal recall tasks," Psychological Bulletin, vol. 132, no. 3, pp. 354-380, 2006.
"""

AUTHOR_YEAR_PAPER = (
    NUMERIC_PAPER.split("References")[0].replace("[1]", "(Roediger & Karpicke, 2006)")
    .replace("[2]", "(Cepeda et al., 2006)")
    + "References\n"
    + "Roediger, H. L., & Karpicke, J. D. (2006). Test-enhanced learning. Psychological Science, "
    "17(3), 249-255.\n"
    + "Cepeda, N. J., Pashler, H., & Rohrer, D. (2006). Distributed practice in verbal recall "
    "tasks. Psychological Bulletin, 132(3), 354-380.\n"
)

RELATED_WORK = (
    "Classroom studies extend laboratory findings on testing to real courses. A meta-analysis of "
    "118 studies reported a mean effect size of g = 0.61 for practice tests [[N1]], which suggests "
    "that the benefit carries over to authentic teaching. Studies in quantitative subjects remain "
    "rare, and most measure recall of facts rather than problem solving. The present study adds a "
    "statistics course in which assessment centres on applying procedures to new problems. It "
    "also contrasts weekly spaced quizzes with review sessions held just before the examination, "
    "a comparison that matches how many instructors already organise their courses. Evidence from "
    "such settings helps instructors judge whether the effort of weekly quizzes is justified."
)


class FakeClient:
    """Answers the literature, plan and section calls; records every call."""

    def __init__(self, *, invent_first: bool = True) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.invent_first = invent_first
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **request):
        name = request["text"]["format"]["name"]
        payload = json.loads(request["input"])
        self.calls.append((name, payload))
        return SimpleNamespace(status="completed", output_text=json.dumps(self._answer(name, payload)))

    def _answer(self, name: str, payload: dict) -> dict:
        if name == "literature":
            return {"sources": [
                {"title": "Rethinking the use of tests: a meta-analysis of practice testing",
                 "authors": ["O. Adesope"], "year": "2017", "venue": "RER", "doi": f"https://doi.org/{META_DOI}",
                 "arxiv_id": "", "url": "", "relevance": "Generality.",
                 "key_points": ["A meta-analysis of 118 studies found g = 0.61 for practice tests."]},
                {"title": "An invented paper", "authors": ["X"], "year": "2024", "venue": "J",
                 "doi": "10.9999/fake", "arxiv_id": "", "url": "", "relevance": "", "key_points": ["None."]},
            ]}
        if name == "revision_plan":
            body = [s for s in payload["manuscript"]["sections"] if s["kind"] == "body"]
            plan = [{"source_id": s["id"], "heading": "", "level": 1, "action": "revise", "goals": [],
                     "addresses": [], "source_keys": [], "table": {"wanted": False, "brief": ""}} for s in body]
            plan.insert(1, {"source_id": "", "heading": "Related Work", "level": 1, "action": "add",
                            "goals": ["Position the study"], "addresses": ["Gap 1"], "source_keys": ["N1"],
                            "table": {"wanted": True, "brief": "Prior evidence"}})
            return {"keywords": ["retrieval practice", "spacing"], "abstract_goals": [], "sections": plan}
        section = payload["section"]
        if section["action"] == "add":
            attempts = sum(1 for n, p in self.calls if n == "revised_section" and p["section"]["action"] == "add")
            text = RELATED_WORK.replace("0.61", "0.75") if self.invent_first and attempts == 1 else RELATED_WORK
            return {"paragraphs": [text, "The evidence is summarised in [[TABLE1]]."],
                    "tables": [{"caption": "Prior evidence", "columns": ["Study", "Finding"],
                                "rows": [["Meta-analysis [[N1]]", "g = 0.61"]], "after_paragraph": 2}],
                    "changes": [{"change": "Added related work", "reason": "Review", "addresses": "Gap 1"}],
                    "author_notes": []}
        return {"paragraphs": [p.replace("strengthens", "improves") for p in section["original_paragraphs"]],
                "tables": [], "changes": [], "author_notes": []}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, "llm_service_mode", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test")
    monkeypatch.setattr(sources, "lookup_doi", lambda doi: REGISTRY.get(doi))
    monkeypatch.setattr(sources, "lookup_arxiv", lambda arxiv_id: None)
    client = FakeClient()
    monkeypatch.setattr(revision_module, "create_openai_client", lambda **_: client)
    return client


def _review(manuscript: Manuscript) -> dict:
    return LLMService._mock_research({"manuscript": manuscript.to_payload()})


# ---------------------------------------------------------------------------
# Verified sources
# ---------------------------------------------------------------------------


def test_identifiers_are_normalised() -> None:
    assert sources.normalize_doi("https://doi.org/10.1103/PhysRevLett.103.150502.") == "10.1103/physrevlett.103.150502"
    assert sources.normalize_doi("no doi here") == ""
    assert sources.normalize_arxiv("https://arxiv.org/abs/2106.12627v2") == "2106.12627"
    assert sources.normalize_arxiv("arXiv:quant-ph/9508027") == "quant-ph/9508027"


def test_crossref_record_is_parsed(monkeypatch) -> None:
    payload = {"message": {
        "DOI": "10.1038/S41586-019-1666-5", "title": ["Quantum supremacy using a programmable <i>superconducting</i> processor"],
        "author": [{"given": "Frank", "family": "Arute"}, {"given": "Kunal", "family": "Arya"}],
        "issued": {"date-parts": [[2019, 10, 23]]}, "container-title": ["Nature"],
        "volume": "574", "issue": "7779", "page": "505-510"}}
    monkeypatch.setattr(sources, "_get", lambda url: json.dumps(payload).encode())
    record = sources.lookup_doi("10.1038/s41586-019-1666-5")
    assert record["title"] == "Quantum supremacy using a programmable superconducting processor"
    assert record["authors"] == ["Frank Arute", "Kunal Arya"]
    assert (record["year"], record["venue"], record["pages"]) == ("2019", "Nature", "505-510")
    assert record["doi"] == "10.1038/s41586-019-1666-5"


def test_arxiv_record_is_parsed(monkeypatch) -> None:
    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>A Quantum Approximate Optimization
        Algorithm</title><published>2014-11-14T19:31:00Z</published>
      <author><name>Edward Farhi</name></author><author><name>Jeffrey Goldstone</name></author>
      </entry></feed>"""
    monkeypatch.setattr(sources, "_get", lambda url: atom)
    record = sources.lookup_arxiv("1411.4028")
    assert record["title"] == "A Quantum Approximate Optimization Algorithm"
    assert record["year"] == "2014" and record["authors"][0] == "Edward Farhi"


def test_unverifiable_or_mismatched_sources_are_dropped(monkeypatch) -> None:
    monkeypatch.setattr(sources, "lookup_doi", lambda doi: REGISTRY.get(doi))
    monkeypatch.setattr(sources, "lookup_arxiv", lambda arxiv_id: None)
    candidates = [
        {"title": "Rethinking the use of tests", "doi": META_DOI, "key_points": ["k"]},
        {"title": "A different paper entirely about birds", "doi": META_DOI},  # DOI of another work
        {"title": "Invented", "doi": "10.9999/none"},
        {"title": "No identifier at all"},
        {"title": "Rethinking the use of tests", "doi": META_DOI.upper()},  # duplicate
    ]
    verified = sources.verify_all(candidates)
    assert [s.key for s in verified] == ["N1"]
    assert verified[0].authors[0] == "Olusola O. Adesope"  # registry metadata, not the model's
    assert sources.verify_all(candidates, existing_titles=[
        "Adesope (2017). Rethinking the Use of Tests: A Meta-Analysis of Practice Testing."]) == []


def test_reference_formats() -> None:
    source = sources.Source.from_dict({**REGISTRY[META_DOI], "key": "N1", "key_points": []})
    assert sources.format_ieee(source).startswith("O. O. Adesope, D. A. Trevisan, and N. Sundararajan, “Rethinking")
    assert "vol. 87, no. 3, pp. 659-701, 2017, doi: 10.3102/0034654316689306." in sources.format_ieee(source)
    assert sources.format_apa(source).startswith("Adesope, O. O., Trevisan, D. A., & Sundararajan, N. (2017).")
    assert sources.in_text_author_year(source) == "Adesope et al., 2017"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def test_plan_keeps_every_section_and_limits_additions(monkeypatch) -> None:
    monkeypatch.setattr(settings, "research_max_new_sections", 1)
    manuscript = parse_manuscript(NUMERIC_PAPER)
    body = [s.id for s in manuscript.sections if s.kind == "body"]
    raw = {"sections": [
        {"source_id": body[2], "heading": "", "level": 1, "action": "revise", "table": {"wanted": True, "brief": "t"}},
        {"source_id": "S99", "heading": "Ghost", "level": 1, "action": "revise"},
        {"source_id": "", "heading": "Related Work", "level": 1, "action": "add", "table": {"wanted": True}},
        {"source_id": "", "heading": "Another", "level": 1, "action": "add"},
        {"source_id": body[2], "heading": "Duplicate", "level": 1, "action": "keep"},
    ]}
    planned, _ = normalize_plan(raw, manuscript, [])
    assert [p.source_id for p in planned].count(body[2]) == 1
    assert {p.source_id for p in planned if p.source_id} == set(body)
    assert [p.heading for p in planned if p.action == "add"] == ["Related Work"]
    assert planned.index(next(p for p in planned if p.source_id == body[0])) < planned.index(
        next(p for p in planned if p.source_id == body[1]))  # forgotten sections keep their order


def test_headings_follow_the_papers_numbering() -> None:
    manuscript = parse_manuscript(NUMERIC_PAPER)
    planned, _ = normalize_plan({"sections": []}, manuscript, [])
    planned.insert(1, revision_module.Planned("", "Related Work", 1, "add"))
    planned.insert(2, revision_module.Planned("", "Prior Meta-Analyses", 2, "add"))
    assert label_headings(planned, manuscript) is True
    assert [p.label for p in planned] == ["1. Introduction", "2. Related Work", "2.1 Prior Meta-Analyses",
                                          "3. Methods", "4. Results"]
    plain = Manuscript("T", [Section("S1", "Introduction", "body", ["x"]), Section("S2", "Methods", "body", ["y"])])
    planned, _ = normalize_plan({"sections": []}, plain, [])
    assert label_headings(planned, plain) is False and planned[0].label == "Introduction"


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


def test_revision_adds_verified_material_in_the_papers_style(live) -> None:
    manuscript = parse_manuscript(NUMERIC_PAPER)
    revision = build_revision(manuscript, _review(manuscript), {"use_web_search": True})
    headings = [s["heading"] for s in revision["sections"] if s["kind"] == "body"]
    assert headings == ["1. Introduction", "2. Related Work", "3. Methods", "4. Results"]
    added = next(s for s in revision["sections"] if s["action"] == "add")
    assert added["status"] == "added"
    assert "g = 0.61 for practice tests [3]" in added["revised"][0]  # verified source -> [3]
    assert "0.75" not in " ".join(added["revised"])  # the invented number was repaired
    assert added["revised"][1] == "The evidence is summarised in Table 1."
    assert added["tables"][0]["rows"][0] == ["Meta-analysis [3]", "g = 0.61"]
    assert [r["number"] for r in revision["new_references"]] == [3]
    references = next(s for s in revision["sections"] if s["kind"] == "references")
    assert references["revised"][:2] == references["original"]
    assert references["revised"][2].startswith("[3] O. O. Adesope")
    assert revision["keywords"] == ["retrieval practice", "spacing"]
    attempts = [p for n, p in live.calls if n == "revised_section" and p["section"]["action"] == "add"]
    assert len(attempts) == 2 and "problems_to_fix" in attempts[1]


def test_author_year_papers_get_author_year_citations(live) -> None:
    manuscript = parse_manuscript(AUTHOR_YEAR_PAPER)
    revision = build_revision(manuscript, _review(manuscript), {"use_web_search": True})
    added = next(s for s in revision["sections"] if s["action"] == "add")
    assert "practice tests (Adesope et al., 2017)" in added["revised"][0]
    references = next(s for s in revision["sections"] if s["kind"] == "references")["revised"]
    assert [entry.split(",")[0] for entry in references] == ["Adesope", "Cepeda", "Roediger"]


def test_no_new_sources_without_web_search(live) -> None:
    manuscript = parse_manuscript(NUMERIC_PAPER)
    revision = build_revision(manuscript, _review(manuscript), {"use_web_search": False})
    assert revision["new_references"] == []
    assert not any(name == "literature" for name, _ in live.calls)
    # The planned section cites N1, which does not exist, so it cannot pass the checks.
    assert "Related Work" in revision["dropped_sections"]


def test_padded_sections_are_rejected(live, monkeypatch) -> None:
    original = FakeClient._answer

    def padded(self, name, payload):
        answer = original(self, name, payload)
        if name == "revised_section" and payload["section"]["action"] == "add":
            sentence = "Weekly quizzes help students remember statistical procedures across the whole term. "
            answer["paragraphs"] = [sentence * 15]
        return answer

    monkeypatch.setattr(FakeClient, "_answer", padded)
    manuscript = parse_manuscript(NUMERIC_PAPER)
    revision = build_revision(manuscript, _review(manuscript), {"use_web_search": True})
    assert revision["dropped_sections"] == ["Related Work"]
    assert [s["heading"] for s in revision["sections"] if s["kind"] == "body"][1] == "2. Methods"


def test_exports_show_new_sections_tables_and_keywords(live, tmp_path: Path) -> None:
    manuscript = parse_manuscript(NUMERIC_PAPER)
    review = _review(manuscript)
    revision = build_revision(manuscript, review, {"use_web_search": True})
    result = {**review, "request": {"author_name": "A. Sen"}, "manuscript": manuscript.to_payload(),
              "revision": revision}
    paths = {p.name.rsplit("-", 1)[-1] if "tracked" not in p.name else "tracked": p
             for p in create_artifacts(result, agent_type="research", output_dir=tmp_path, author="A. Sen")}
    clean = Document(next(p for p in tmp_path.glob("*-revised.docx")))
    text = "\n".join(p.text for p in clean.paragraphs)
    assert "2. Related Work" in text and "Table 1. Prior evidence" in text
    assert "Keywords: retrieval practice; spacing" in text
    assert clean.tables[0].rows[1].cells[0].text == "Meta-analysis [3]"
    with zipfile.ZipFile(paths["tracked"]) as archive:
        document = archive.read("word/document.xml").decode()
    assert "<w:trPr><w:ins " in document  # table rows are tracked insertions
    assert "Related Work" in document and "Keywords: " in document
    report = Document(next(p for p in tmp_path.glob("*-review.docx")))
    cells = " ".join(c.text for t in report.tables for r in t.rows for c in r.cells)
    assert "Rethinking the Use of Tests" in cells and "New section" in cells
