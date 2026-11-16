ALTER TABLE questions ADD COLUMN reference_key TEXT;
ALTER TABLE questions ADD COLUMN subsection TEXT;
ALTER TABLE questions ADD COLUMN min_selections INTEGER;
ALTER TABLE questions ADD COLUMN scale_min_label TEXT;
ALTER TABLE questions ADD COLUMN scale_max_label TEXT;
CREATE UNIQUE INDEX questions_reference_key_key ON questions(reference_key);
ALTER TABLE questions ADD CONSTRAINT question_min_selections CHECK (
  min_selections IS NULL OR (
    answer_type = 'multi_choice' AND min_selections > 0
    AND max_selections IS NOT NULL AND min_selections <= max_selections
  )
);
ALTER TABLE question_options ADD COLUMN reference_key TEXT;
ALTER TABLE reports ADD COLUMN ruleset_version TEXT;
