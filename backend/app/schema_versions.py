"""Explicit migration versions the structured path needs, in order.

Kept inside backend/ because the API image does not contain scripts/. The
runner scripts/migrate_document_snapshots.py applies exactly these versions; a
test checks that the two lists agree.
"""
REQUIRED_SCHEMA_VERSIONS = (
    'm1_document_snapshots_v1',
    'm2_structured_ingest_v1',
    'm2c_structured_review_v1',
    'm3a_pdf_parse_v1',
    'm3b_pdf_index_v1',
    'm4c_research_persistence_v1',
)

REQUIRED_STRUCTURED_SCHEMA_VERSIONS = REQUIRED_SCHEMA_VERSIONS[:-1]

