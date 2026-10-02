"""A2 (second half): freeze the acceptance set, its checks and the questions.

Pages are chosen from the source content and the preview, never from a Docling
result. Expected values are read out of the source XML with the explicit ODF
whitespace nodes decoded, so tabs and repeated spaces survive into the check.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import NS, sha256_json, utc_now
from .inventory import Q, _text_of, inspect_file

SQL_PATTERN = re.compile(r"\b(SELECT|FROM|WHERE|CREATE TABLE|INSERT INTO|GROUP BY|ORDER BY|HAVING|ALTER TABLE|UPDATE)\b", re.I)

DEVELOPMENT_DECKS = [
    "CoursInfoDeBase-2022_SQL.odp",
    "Algebre_relationnelle_2020.odp",
    "Algorithme_Graphe_2022-2023.odp",
]

# 12 development pages. Chosen for category coverage from the source inventory.
DEVELOPMENT_PAGES = [
    ("CoursInfoDeBase-2022_SQL.odp", 7, ["code_sql", "indentation", "grouped_shape", "text_list"]),
    ("CoursInfoDeBase-2022_SQL.odp", 12, ["text_list", "indentation"]),
    ("CoursInfoDeBase-2022_SQL.odp", 13, ["code_sql", "indentation"]),
    ("CoursInfoDeBase-2022_SQL.odp", 29, ["code_sql", "indentation", "text_list"]),
    ("CoursInfoDeBase-2022_SQL.odp", 35, ["code_sql", "indentation", "grouped_shape"]),
    ("CoursInfoDeBase-2022_SQL.odp", 47, ["code_sql", "grouped_shape", "complex_reading_order"]),
    ("Algebre_relationnelle_2020.odp", 6, ["table", "text_list"]),
    ("Algebre_relationnelle_2020.odp", 10, ["code_sql", "indentation", "text_list"]),
    ("Algebre_relationnelle_2020.odp", 13, ["table"]),
    ("Algebre_relationnelle_2020.odp", 19, ["indentation", "text_list"]),
    ("Algorithme_Graphe_2022-2023.odp", 6, ["figure_embedded_object", "complex_reading_order", "text_list"]),
    ("Algorithme_Graphe_2022-2023.odp", 15, ["figure_embedded_object", "text_list"]),
]

# 6 held-out pages: at least one from each of the other five decks, plus the most
# complex remaining page by object count (Arbres_Naires slide 2).
HOLDOUT_PAGES = [
    ("Algorithmes_Arborescences_2022-2023.odp", 5, ["figure_embedded_object", "complex_reading_order"]),
    ("Arbres_Naires_2022-2023.odp", 7, ["figure_embedded_object", "grouped_shape", "complex_reading_order"]),
    ("CoursInfoDeBase-2020.odp", 11, ["text_list", "indentation"]),
    ("De_LAlgebre_Relationnel_au_SQL_2022.odp", 9, ["code_sql", "indentation"]),
    ("Primitives_GrapheEtArbre_2022-2023.odp", 1, ["figure_embedded_image", "figure_embedded_object", "text_list"]),
    ("Arbres_Naires_2022-2023.odp", 2, ["figure_embedded_object", "complex_reading_order", "text_list"]),
]

# Categories the plan asks for that no real slide in this corpus provides.
NOT_PRESENT = {
    "formula": "no ODF formula object and no MathML in any of the eight files; covered by fixture only",
    "hidden_slide": "no slide in the corpus is hidden; covered by fixture only",
    "speaker_notes": "all 187 notes nodes are empty; covered by fixture only",
}


def _embedded_table_values(odp: Path, href: str, limit: int = 12) -> list[str]:
    """Cell values of an embedded spreadsheet, used as expected table content."""
    folder = href.lstrip("./").rstrip("/")
    with zipfile.ZipFile(odp) as archive:
        name = f"{folder}/content.xml"
        if name not in archive.namelist():
            return []
        root = ET.fromstring(archive.read(name))
    values = []
    for cell in root.iter(Q["table"] + "table-cell"):
        text = _text_of(cell).strip()
        if text:
            values.append(text)
        if len(values) >= limit:
            break
    return values


def _page_checks(deck: str, page: dict, categories: list[str], odp: Path) -> list[dict]:
    checks: list[dict] = []
    slide_no = page["source_slide_no"]
    prefix = f"{Path(deck).stem}-p{slide_no:02d}"
    shapes = [o for o in page["objects"] if o["role"] == "shape"]

    for obj in shapes:
        for position, line in enumerate(obj["text_sample"].splitlines(), start=1):
            if len(line.strip()) < 8:
                continue
            is_code = bool(SQL_PATTERN.search(line))
            has_whitespace = "\t" in line or re.search(r"  +", line) is not None
            checks.append({
                "check_id": f"{prefix}-text-{len(checks) + 1:03d}",
                "deck": deck,
                "source_slide_no": slide_no,
                "category": "code_sql" if is_code else "text_list",
                "importance": "high" if is_code or has_whitespace else "normal",
                "verification": "automatic",
                "assertion": "exact_text_present",
                "expected": line,
                "expected_keeps_whitespace": has_whitespace,
                "source_object_path": obj["object_path"],
                "source_paragraph_no": position,
            })

    for obj in shapes:
        if obj["kind"] == "embedded_spreadsheet" and obj["href"]:
            values = _embedded_table_values(odp, obj["href"])
            checks.append({
                "check_id": f"{prefix}-table-{len(checks) + 1:03d}",
                "deck": deck,
                "source_slide_no": slide_no,
                "category": "table",
                "importance": "high",
                "verification": "automatic",
                "assertion": "table_cells_present",
                "expected": values,
                "source_object_path": obj["object_path"],
                "source_paragraph_no": None,
            })
        if obj["kind"] in {"embedded_graphics", "embedded_image", "group_shape"}:
            checks.append({
                "check_id": f"{prefix}-figure-{len(checks) + 1:03d}",
                "deck": deck,
                "source_slide_no": slide_no,
                "category": "figure_embedded_image" if obj["kind"] == "embedded_image" else "figure_embedded_object",
                "importance": "high",
                "verification": "automatic",
                "assertion": "figure_block_with_asset",
                "expected": {"source_object_kind": obj["kind"], "source_object_path": obj["object_path"]},
                "source_object_path": obj["object_path"],
                "source_paragraph_no": None,
            })

    checks.append({
        "check_id": f"{prefix}-source-{len(checks) + 1:03d}",
        "deck": deck,
        "source_slide_no": slide_no,
        "category": "source_identity",
        "importance": "high",
        "verification": "automatic",
        "assertion": "every_block_has_source_object",
        "expected": None,
        "source_object_path": None,
        "source_paragraph_no": None,
    })
    checks.append({
        "check_id": f"{prefix}-page-{len(checks) + 1:03d}",
        "deck": deck,
        "source_slide_no": slide_no,
        "category": "page_reference",
        "importance": "high",
        "verification": "automatic",
        "assertion": "page_reference_resolves",
        "expected": None,
        "source_object_path": None,
        "source_paragraph_no": None,
    })
    checks.append({
        "check_id": f"{prefix}-hidden-{len(checks) + 1:03d}",
        "deck": deck,
        "source_slide_no": slide_no,
        "category": "visibility",
        "importance": "high",
        "verification": "automatic",
        "assertion": "no_notes_or_hidden_text_in_student_view",
        "expected": None,
        "source_object_path": None,
        "source_paragraph_no": None,
    })
    for category in ("reading_order", "figure_meaning", "code_symbols"):
        if category == "figure_meaning" and not any(c.startswith("figure") for c in categories):
            continue
        if category == "code_symbols" and "code_sql" not in categories:
            continue
        checks.append({
            "check_id": f"{prefix}-human-{category}",
            "deck": deck,
            "source_slide_no": slide_no,
            "category": category,
            "importance": "high",
            "verification": "human",
            "assertion": {
                "reading_order": "blocks follow the order a student reads the slide in",
                "figure_meaning": "the figure keeps its meaning: directions and labels are not reversed",
                "code_symbols": "code symbols and indentation are not damaged",
            }[category],
            "expected": None,
            "source_object_path": None,
            "source_paragraph_no": None,
        })
    return checks


def build_suite(source_dir: Path) -> tuple[dict, dict]:
    inventories = {}
    for deck in {name for name, _, _ in DEVELOPMENT_PAGES + HOLDOUT_PAGES}:
        inventories[deck] = inspect_file(Path(source_dir) / deck)

    pages = []
    checks = []
    for split, selection in (("development", DEVELOPMENT_PAGES), ("holdout", HOLDOUT_PAGES)):
        for deck, slide_no, categories in selection:
            inventory_file = inventories[deck]
            page = inventory_file["pages"][slide_no - 1]
            pages.append({
                "split": split,
                "deck": deck,
                "deck_sha256": inventory_file["sha256"],
                "source_slide_no": slide_no,
                "visibility": page["visibility"],
                "title_candidate": page["title_candidate"],
                "categories": categories,
                "object_counts": page["object_counts"],
            })
            checks.extend(_page_checks(deck, page, categories, Path(source_dir) / deck))

    suite = {
        "generated_at": utc_now(),
        "frozen": True,
        "development_decks": DEVELOPMENT_DECKS,
        "page_count": len(pages),
        "development_page_count": sum(1 for p in pages if p["split"] == "development"),
        "holdout_page_count": sum(1 for p in pages if p["split"] == "holdout"),
        "categories_not_present": NOT_PRESENT,
        "coverage_note": (
            "The three development decks contain no embedded bitmap of their own, "
            "so figure_embedded_image is covered by held-out pages and fixtures only."
        ),
        "pages": pages,
    }
    check_set = {
        "generated_at": utc_now(),
        "frozen": True,
        "check_count": len(checks),
        "automatic_count": sum(1 for c in checks if c["verification"] == "automatic"),
        "human_count": sum(1 for c in checks if c["verification"] == "human"),
        "checks": checks,
    }
    suite["suite_sha256"] = sha256_json(suite["pages"])
    check_set["checks_sha256"] = sha256_json(check_set["checks"])
    return suite, check_set


QUESTIONS = [
    {
        "question_id": "Q01",
        "question": "Quelle est la syntaxe générale d'une requête SELECT et dans quel ordre écrit-on les clauses ?",
        "evidence": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "source_slide_no": 6}],
        "needs_figure": False, "needs_adjacent_block": True,
    },
    {
        "question_id": "Q02",
        "question": "Comment écrire une clause SELECT qui supprime les doublons, et sur quel exemple le cours le montre-t-il ?",
        "evidence": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "source_slide_no": 7},
                      {"deck": "CoursInfoDeBase-2022_SQL.odp", "source_slide_no": 8}],
        "needs_figure": False, "needs_adjacent_block": True,
    },
    {
        "question_id": "Q03",
        "question": "Que vaut une expression qui contient NULL, et à quoi sert NVL ?",
        "evidence": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "source_slide_no": 12}],
        "needs_figure": False, "needs_adjacent_block": False,
    },
    {
        "question_id": "Q04",
        "question": "Comment crée-t-on une table avec une clé primaire en SQL dans ce cours ?",
        "evidence": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "source_slide_no": 35},
                      {"deck": "CoursInfoDeBase-2022_SQL.odp", "source_slide_no": 36}],
        "needs_figure": False, "needs_adjacent_block": True,
    },
    {
        "question_id": "Q05",
        "question": "Quelle est la différence entre la clause WHERE et la clause HAVING ?",
        "evidence": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "source_slide_no": 14},
                      {"deck": "CoursInfoDeBase-2022_SQL.odp", "source_slide_no": 28}],
        "needs_figure": False, "needs_adjacent_block": True,
    },
    {
        "question_id": "Q06",
        "question": "Qu'est-ce que la jointure naturelle en algèbre relationnelle, et sur quel exemple de tables est-elle illustrée ?",
        "evidence": [{"deck": "Algebre_relationnelle_2020.odp", "source_slide_no": 12},
                      {"deck": "Algebre_relationnelle_2020.odp", "source_slide_no": 13}],
        "needs_figure": False, "needs_adjacent_block": True,
    },
    {
        "question_id": "Q07",
        "question": "Que produit le produit cartésien de deux relations, avec l'exemple chiffré du cours ?",
        "evidence": [{"deck": "Algebre_relationnelle_2020.odp", "source_slide_no": 19},
                      {"deck": "Algebre_relationnelle_2020.odp", "source_slide_no": 20}],
        "needs_figure": False, "needs_adjacent_block": True,
    },
    {
        "question_id": "Q08",
        "question": "Dans l'algorithme de Dijkstra, comment choisit-on le nouveau nœud courant, et que montre le schéma de cette étape ?",
        "evidence": [{"deck": "Algorithme_Graphe_2022-2023.odp", "source_slide_no": 6}],
        "needs_figure": True, "needs_adjacent_block": False,
    },
    {
        "question_id": "Q09",
        "question": "Comment l'algorithme de Prim construit-il l'arbre de recouvrement de poids minimal, étape par étape ?",
        "evidence": [{"deck": "Algorithme_Graphe_2022-2023.odp", "source_slide_no": 15},
                      {"deck": "Algorithme_Graphe_2022-2023.odp", "source_slide_no": 16}],
        "needs_figure": True, "needs_adjacent_block": True,
    },
    {
        "question_id": "Q10",
        "question": "Quelle opération SQL correspond à la restriction de l'algèbre relationnelle ?",
        "evidence": [{"deck": "De_LAlgebre_Relationnel_au_SQL_2022.odp", "source_slide_no": 9}],
        "needs_figure": False, "needs_adjacent_block": False,
    },
]


def build_questions() -> dict:
    return {
        "generated_at": utc_now(),
        "frozen": True,
        "purpose": "check that the evidence a question needs survives parsing. No agent is run and no answer accuracy is reported.",
        "question_count": len(QUESTIONS),
        "questions": QUESTIONS,
        "questions_sha256": sha256_json(QUESTIONS),
    }
