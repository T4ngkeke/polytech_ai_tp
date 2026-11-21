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
    'm4e_clarification_v1',
    'm4f_answer_modes_v1',
    'm4g_picture_captions_v1',
    'm4h_job_not_before_v1',
    'm4i_quota_week_v1',
    'm4j_rule_items_v1',
    'm4k_exam_files_v1',
    'm4l_tour_v1',
    'm4m_rule_source_v1',
    'm4n_reset_outcome_v1',
    'm4o_must_change_password_v1',
    'm4p_features_v1',
    'm4q_notifications_v1',
    'm4r_user_settings_v1',
    'm4s_chat_files_v1',
    'm4t_message_versions_v1',
    'm4u_student_memories_v1',
    'm4v_course_use_v1',
    'm4w_task_checklist_v1',
    'm4x_web_sources_v1',
    'm4y_code_runs_v1',
    'm4z_follow_ups_v1',
)

# Structured ingestion alone needs the versions before the research ones.
REQUIRED_STRUCTURED_SCHEMA_VERSIONS = REQUIRED_SCHEMA_VERSIONS[
    :REQUIRED_SCHEMA_VERSIONS.index('m4c_research_persistence_v1')]

