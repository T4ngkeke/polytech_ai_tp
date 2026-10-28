-- M4-G additive: picture captions written by a model at ingest, kept by the
-- hash of the picture's pixels so a new version of a course reuses them.
-- A caption describes pixels, not a course: the table is shared by all
-- documents. Apply after M4-F.
-- statement
CREATE TABLE IF NOT EXISTS picture_captions (
    image_sha256 VARCHAR(64) NOT NULL,
    model VARCHAR(128) NOT NULL,
    prompt_version VARCHAR(32) NOT NULL,
    caption TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (image_sha256, model, prompt_version)
);
