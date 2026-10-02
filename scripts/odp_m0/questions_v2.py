"""Question evidence, named exactly.

The first round asked only whether the evidence page produced any text. That
passes even when the sentence the question needs is gone. Here each question
names the paragraph it needs, by an anchor that is looked up in the source
inventory so the expected value is the source text character for character.
"""
from __future__ import annotations

from .common import sha256_json, utc_now

# anchor -> the source paragraph containing it is the required text.
NEEDS = {
    "Q01": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "slide": 6,
             "anchors": ["L'ordre des clauses est imposé", "GROUP BY"], "figure": False, "table": False}],
    "Q02": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "slide": 7,
             "anchors": ["[distinct] expression1", "permet de ne garder que les lignes différentes"],
             "figure": False, "table": False},
            {"deck": "CoursInfoDeBase-2022_SQL.odp", "slide": 8,
             "anchors": ["se trouvant à la suite du select"], "figure": False, "table": False}],
    "Q03": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "slide": 12,
             "anchors": ["NVL(taxe, 0)", "Toute expression qui contient la valeur NULL"],
             "figure": False, "table": False}],
    "Q04": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "slide": 35,
             "anchors": ["varchar(50) not null", "colonne1 type1,"], "figure": False, "table": False},
            {"deck": "CoursInfoDeBase-2022_SQL.odp", "slide": 36,
             "anchors": ["PRIMARY KEY"], "figure": False, "table": False}],
    "Q05": [{"deck": "CoursInfoDeBase-2022_SQL.odp", "slide": 14,
             "anchors": ["La clause WHERE comporte"], "figure": False, "table": False},
            {"deck": "CoursInfoDeBase-2022_SQL.odp", "slide": 28,
             "anchors": ["HAVING prédicat", "caractéristiques de groupe"], "figure": False, "table": False}],
    "Q06": [{"deck": "Algebre_relationnelle_2020.odp", "slide": 12,
             "anchors": ["Notation", "opération consistant à rapprocher"], "figure": False, "table": False},
            {"deck": "Algebre_relationnelle_2020.odp", "slide": 13,
             "anchors": [], "figure": False, "table": True}],
    "Q07": [{"deck": "Algebre_relationnelle_2020.odp", "slide": 19,
             "anchors": ["product(r, s)", "opération sur deux relations"], "figure": False, "table": False},
            {"deck": "Algebre_relationnelle_2020.odp", "slide": 20,
             "anchors": [], "figure": False, "table": True}],
    "Q08": [{"deck": "Algorithme_Graphe_2022-2023.odp", "slide": 6,
             "anchors": ["la plus petite distance", "extraction du nœud courant"],
             "figure": True, "table": False}],
    "Q09": [{"deck": "Algorithme_Graphe_2022-2023.odp", "slide": 15,
             "anchors": ["L'ensemble des arcs utilisables"], "figure": True, "table": False},
            {"deck": "Algorithme_Graphe_2022-2023.odp", "slide": 16,
             "anchors": ["Extraction de l'arc de poids minimal"], "figure": True, "table": False}],
    "Q10": [{"deck": "De_LAlgebre_Relationnel_au_SQL_2022.odp", "slide": 9,
             "anchors": ["where Vins1.mill", "Restrict(Vin1"], "figure": False, "table": False}],
}


def _paragraph_for(anchor: str, page: dict) -> str | None:
    for obj in page["objects"]:
        if obj["role"] != "shape":
            continue
        for line in obj["text_sample"].splitlines():
            if anchor in line:
                return line
    return None


def build(inventory_by_deck: dict) -> dict:
    """Resolve every anchor to the exact source paragraph."""
    out = []
    for question_id, needs in NEEDS.items():
        resolved = []
        for need in needs:
            record = inventory_by_deck[need["deck"]]
            page = record["pages"][need["slide"] - 1]
            texts = []
            for anchor in need["anchors"]:
                paragraph = _paragraph_for(anchor, page)
                texts.append({"anchor": anchor, "required_text": paragraph,
                              "resolved": paragraph is not None})
            figures = [o["object_path"] for o in page["objects"]
                       if o["role"] == "shape" and o["kind"] in {"embedded_graphics", "embedded_image", "group_shape"}]
            tables = [o["object_path"] for o in page["objects"]
                      if o["role"] == "shape" and o["kind"] == "embedded_spreadsheet"]
            resolved.append({
                "deck": need["deck"], "source_slide_no": need["slide"],
                "required_texts": texts,
                "required_figure": need["figure"], "figure_objects": figures,
                "required_table": need["table"], "table_objects": tables,
            })
        out.append({"question_id": question_id, "needs": resolved})
    payload = {
        "generated_at": utc_now(),
        "frozen": True,
        "note": ("Each question names the paragraph, table or figure it needs. Evidence presence only: "
                 "no retrieval is run and no answer accuracy is reported."),
        "questions": out,
    }
    payload["questions_v2_sha256"] = sha256_json(out)
    return payload
