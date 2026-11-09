"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-19

Transcribed from docs/schema.md, which carries the reasoning for every decision here.
Written by hand: autogenerate cannot see the triggers, functions, functional indexes or CHECK
constraints below, which is where most of this schema's integrity lives.

Ordering is tables first (in dependency order), then functions, then triggers.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ enums
    op.execute("""
        CREATE TYPE question_category AS ENUM ('interests', 'strengths', 'eq');

        CREATE TYPE answer_type AS ENUM ('single_choice', 'multi_choice', 'scale', 'free_text');

        CREATE TYPE holland_trait AS ENUM (
            'realistic', 'investigative', 'artistic',
            'social', 'enterprising', 'conventional'
        );

        CREATE TYPE report_tier AS ENUM ('free', 'full');

        CREATE TYPE entitlement_kind AS ENUM ('single_report', 'window', 'coaching_session');

        CREATE TYPE admin_permission AS ENUM (
            'author_questionnaire',
            'publish_questionnaire',
            'manage_careers',
            'manage_discount_codes',
            'view_user_data',
            'manage_purchases',
            'manage_admins'
        );
    """)

    # ------------------------------------------------------------------ identity
    op.execute("""
        CREATE TABLE users (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at    timestamptz NOT NULL DEFAULT now(),
            name          text        NOT NULL,
            email         text        NOT NULL,
            school        text,
            google_sub    text UNIQUE
        );

        CREATE UNIQUE INDEX users_email_lower_idx ON users (lower(email));
        CREATE INDEX        users_created_at_idx  ON users (created_at DESC);

        CREATE TABLE admin_accounts (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name           text NOT NULL,
            email          text NOT NULL,
            google_sub     text UNIQUE,
            permissions    admin_permission[] NOT NULL DEFAULT '{}',
            created_at     timestamptz NOT NULL DEFAULT now(),
            created_by     uuid REFERENCES admin_accounts (id) ON DELETE RESTRICT,
            deactivated_at timestamptz
        );

        CREATE UNIQUE INDEX admin_accounts_email_lower_idx ON admin_accounts (lower(email));
        CREATE INDEX admin_accounts_permissions_idx ON admin_accounts USING gin (permissions);
    """)

    # ------------------------------------------------------------------ questionnaire definition
    op.execute("""
        CREATE TABLE questionnaires (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            nickname        text        NOT NULL,
            version         text        NOT NULL,
            derived_from_id uuid REFERENCES questionnaires (id) ON DELETE SET NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            created_by      uuid REFERENCES admin_accounts (id) ON DELETE RESTRICT,
            published_at    timestamptz,
            published_by    uuid REFERENCES admin_accounts (id) ON DELETE RESTRICT,

            UNIQUE (nickname, version),
            CONSTRAINT questionnaires_publish_attribution
                CHECK ((published_at IS NULL) = (published_by IS NULL))
        );

        CREATE TABLE questions (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            questionnaire_id uuid NOT NULL REFERENCES questionnaires (id) ON DELETE CASCADE,
            position         int  NOT NULL,

            category       question_category NOT NULL,
            answer_type    answer_type NOT NULL,
            prompt         text NOT NULL,
            help_text      text,
            trait          holland_trait,

            scale_min      smallint,
            scale_max      smallint,
            reverse_scored boolean NOT NULL DEFAULT false,

            max_selections smallint,

            created_at     timestamptz NOT NULL DEFAULT now(),
            created_by     uuid REFERENCES admin_accounts (id) ON DELETE RESTRICT,
            updated_at     timestamptz,
            updated_by     uuid REFERENCES admin_accounts (id) ON DELETE RESTRICT,

            CONSTRAINT questions_update_attribution
                CHECK ((updated_at IS NULL) = (updated_by IS NULL)),
            CONSTRAINT questions_scale_bounds CHECK (
                CASE WHEN answer_type = 'scale'
                     THEN scale_min IS NOT NULL AND scale_max IS NOT NULL
                          AND scale_max > scale_min
                     ELSE scale_min IS NULL AND scale_max IS NULL AND reverse_scored IS FALSE
                END
            ),
            CONSTRAINT questions_max_selections CHECK (
                CASE WHEN answer_type = 'multi_choice'
                     THEN max_selections IS NULL OR max_selections > 0
                     ELSE max_selections IS NULL
                END
            ),

            UNIQUE (questionnaire_id, position) DEFERRABLE INITIALLY DEFERRED,
            UNIQUE (questionnaire_id, id),
            UNIQUE (id, answer_type)
        );

        CREATE TABLE question_options (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            question_id uuid NOT NULL,
            answer_type answer_type NOT NULL,
            position    int  NOT NULL,
            label       text NOT NULL,
            score_value smallint,
            trait       holland_trait,

            UNIQUE (question_id, position) DEFERRABLE INITIALLY DEFERRED,
            UNIQUE (question_id, id),

            FOREIGN KEY (question_id, answer_type)
                REFERENCES questions (id, answer_type) ON DELETE CASCADE,
            CONSTRAINT question_options_choice_only
                CHECK (answer_type IN ('single_choice', 'multi_choice'))
        );
    """)

    # ------------------------------------------------------------------ responses
    op.execute("""
        CREATE TABLE questionnaire_responses (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id          uuid NOT NULL REFERENCES users (id)          ON DELETE CASCADE,
            questionnaire_id uuid NOT NULL REFERENCES questionnaires (id) ON DELETE RESTRICT,

            started_at             timestamptz NOT NULL DEFAULT now(),
            interests_completed_at timestamptz,
            strengths_completed_at timestamptz,
            eq_completed_at        timestamptz,

            UNIQUE (id, questionnaire_id)
        );

        CREATE INDEX questionnaire_responses_user_idx
            ON questionnaire_responses (user_id, started_at DESC);

        CREATE TABLE response_answers (
            response_id      uuid NOT NULL,
            questionnaire_id uuid NOT NULL,
            question_id      uuid NOT NULL,
            answer_numeric   smallint,
            answer_text      text,
            answered_at      timestamptz NOT NULL DEFAULT now(),

            PRIMARY KEY (response_id, question_id),

            FOREIGN KEY (response_id, questionnaire_id)
                REFERENCES questionnaire_responses (id, questionnaire_id) ON DELETE CASCADE,
            FOREIGN KEY (questionnaire_id, question_id)
                REFERENCES questions (questionnaire_id, id) ON DELETE RESTRICT
        );

        CREATE TABLE response_answer_options (
            response_id uuid NOT NULL,
            question_id uuid NOT NULL,
            option_id   uuid NOT NULL,

            PRIMARY KEY (response_id, question_id, option_id),
            FOREIGN KEY (response_id, question_id)
                REFERENCES response_answers (response_id, question_id) ON DELETE CASCADE,
            FOREIGN KEY (question_id, option_id)
                REFERENCES question_options (question_id, id) ON DELETE RESTRICT
        );

        CREATE INDEX response_answer_options_option_idx
            ON response_answer_options (question_id, option_id);
    """)

    # ------------------------------------------------------------------ careers reference data
    op.execute("""
        CREATE TABLE career_families (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slug            text NOT NULL UNIQUE,
            name            text NOT NULL,
            description     text NOT NULL,
            example_careers text[] NOT NULL DEFAULT '{}',
            holland_code    holland_trait[] NOT NULL DEFAULT '{}',
            is_active       boolean NOT NULL DEFAULT true,

            CONSTRAINT career_families_holland_len CHECK (
                array_length(holland_code, 1) IS NULL
                OR array_length(holland_code, 1) BETWEEN 1 AND 3
            )
        );

        CREATE INDEX career_families_holland_idx ON career_families USING gin (holland_code);

        CREATE TABLE career_pay (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            normalized_name text NOT NULL,
            region          text NOT NULL,
            currency        char(3) NOT NULL,

            pay_low         int,
            pay_median      int,
            pay_high        int,

            source          text NOT NULL,
            occupation_code text,
            source_url      text,
            as_of           date NOT NULL,
            fetched_at      timestamptz NOT NULL DEFAULT now(),

            UNIQUE (normalized_name, region, source),
            CONSTRAINT career_pay_source CHECK (source IN ('bls_oews', 'model_research')),
            CONSTRAINT career_pay_order CHECK (
                (pay_low IS NULL OR pay_median IS NULL OR pay_median >= pay_low)
                AND (pay_median IS NULL OR pay_high IS NULL OR pay_high >= pay_median)
            )
        );

        CREATE INDEX career_pay_lookup_idx ON career_pay (normalized_name, region);
    """)

    # ------------------------------------------------------------------ reports
    op.execute("""
        CREATE TABLE reports (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            response_id     uuid NOT NULL REFERENCES questionnaire_responses (id)
                                ON DELETE CASCADE,
            tier            report_tier NOT NULL,
            generated_at    timestamptz NOT NULL DEFAULT now(),
            scoring_version text NOT NULL,
            model_version   text,
            input_hash      text NOT NULL,
            payload         jsonb NOT NULL,
            emailed_at      timestamptz
        );

        CREATE INDEX reports_response_idx   ON reports (response_id, generated_at DESC);
        CREATE INDEX reports_input_hash_idx ON reports (input_hash, scoring_version);

        CREATE TABLE report_career_families (
            report_id uuid NOT NULL REFERENCES reports (id)         ON DELETE CASCADE,
            family_id uuid NOT NULL REFERENCES career_families (id) ON DELETE RESTRICT,
            rank      int  NOT NULL,
            fit_score numeric(4,3),

            PRIMARY KEY (report_id, family_id),
            UNIQUE (report_id, rank) DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT report_career_families_fit_range CHECK (
                fit_score IS NULL OR fit_score BETWEEN 0 AND 1
            )
        );

        CREATE INDEX report_career_families_family_idx ON report_career_families (family_id);
    """)

    # ------------------------------------------------------------------ payments
    op.execute("""
        CREATE TABLE discount_codes (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            code             text NOT NULL,
            stripe_coupon_id text,
            starts_at        timestamptz,
            ends_at          timestamptz,
            max_redemptions  int,

            CONSTRAINT discount_codes_window CHECK (
                ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at
            )
        );

        CREATE UNIQUE INDEX discount_codes_code_upper_idx ON discount_codes (upper(code));

        CREATE TABLE purchases (
            id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                    uuid NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            stripe_checkout_session_id text UNIQUE,
            stripe_payment_intent_id   text UNIQUE,
            stripe_price_id            text,
            amount_cents               int  NOT NULL,
            currency                   char(3) NOT NULL DEFAULT 'usd',
            discount_code_id           uuid REFERENCES discount_codes (id) ON DELETE SET NULL,
            paid_at                    timestamptz,
            refunded_at                timestamptz,
            created_at                 timestamptz NOT NULL DEFAULT now()
        );

        CREATE INDEX purchases_user_idx ON purchases (user_id, created_at DESC);

        CREATE TABLE entitlements (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     uuid NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            kind        entitlement_kind NOT NULL,

            purchase_id uuid REFERENCES purchases (id)      ON DELETE RESTRICT,
            granted_by  uuid REFERENCES admin_accounts (id) ON DELETE RESTRICT,
            grant_note  text,

            response_id uuid REFERENCES questionnaire_responses (id) ON DELETE RESTRICT,
            valid_from  timestamptz NOT NULL DEFAULT now(),
            valid_until timestamptz,
            consumed_at timestamptz,
            revoked_at  timestamptz,
            created_at  timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT entitlements_origin CHECK (
                (purchase_id IS NULL) <> (granted_by IS NULL)
            ),
            CONSTRAINT entitlements_shape CHECK (
                CASE kind
                    WHEN 'single_report'    THEN response_id IS NOT NULL
                    WHEN 'window'           THEN response_id IS NULL AND valid_until IS NOT NULL
                    WHEN 'coaching_session' THEN response_id IS NULL
                END
            ),
            CONSTRAINT entitlements_validity CHECK (
                valid_until IS NULL OR valid_until > valid_from
            )
        );

        CREATE INDEX entitlements_lookup_idx ON entitlements (user_id) WHERE revoked_at IS NULL;
    """)

    # ------------------------------------------------------------------ immutability triggers
    # A published questionnaire is frozen entirely: the row, its questions, and their options.
    # "Reject this write" is not expressible as a constraint, hence triggers.
    op.execute("""
        CREATE FUNCTION questionnaires_reject_published_change() RETURNS trigger AS $$
        BEGIN
            IF OLD.published_at IS NOT NULL THEN
                RAISE EXCEPTION 'questionnaire % is published and is immutable', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;

            RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE FUNCTION questions_reject_published_change() RETURNS trigger AS $$
        DECLARE
            affected uuid[];
        BEGIN
            affected := CASE TG_OP
                WHEN 'INSERT' THEN ARRAY[NEW.questionnaire_id]
                WHEN 'DELETE' THEN ARRAY[OLD.questionnaire_id]
                ELSE               ARRAY[OLD.questionnaire_id, NEW.questionnaire_id]
            END;

            IF EXISTS (
                SELECT 1 FROM questionnaires
                WHERE id = ANY (affected) AND published_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'questionnaire is published; copy it into a new draft version to make changes'
                    USING ERRCODE = 'restrict_violation';
            END IF;

            RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE FUNCTION question_options_reject_published_change() RETURNS trigger AS $$
        DECLARE
            affected uuid[];
        BEGIN
            affected := CASE TG_OP
                WHEN 'INSERT' THEN ARRAY[NEW.question_id]
                WHEN 'DELETE' THEN ARRAY[OLD.question_id]
                ELSE               ARRAY[OLD.question_id, NEW.question_id]
            END;

            IF EXISTS (
                SELECT 1
                FROM questions q
                JOIN questionnaires n ON n.id = q.questionnaire_id
                WHERE q.id = ANY (affected) AND n.published_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'questionnaire is published; copy it into a new draft version to make changes'
                    USING ERRCODE = 'restrict_violation';
            END IF;

            RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER questionnaires_immutable_after_publish
            BEFORE UPDATE OR DELETE ON questionnaires
            FOR EACH ROW EXECUTE FUNCTION questionnaires_reject_published_change();

        CREATE TRIGGER questions_immutable_after_publish
            BEFORE INSERT OR UPDATE OR DELETE ON questions
            FOR EACH ROW EXECUTE FUNCTION questions_reject_published_change();

        CREATE TRIGGER question_options_immutable_after_publish
            BEFORE INSERT OR UPDATE OR DELETE ON question_options
            FOR EACH ROW EXECUTE FUNCTION question_options_reject_published_change();
    """)

    # ------------------------------------------------------------------ fork_questionnaire
    # MAINTENANCE HAZARD: both INSERT ... SELECT statements enumerate their columns.
    # Adding a column to `questions` or `question_options` without adding it here is a silent
    # data-loss bug — the copy is structurally complete and quietly wrong. The
    # structural-equality test is what catches it. See docs/schema.md.
    op.execute("""
        CREATE FUNCTION fork_questionnaire(source_id uuid, new_version text, actor_id uuid)
        RETURNS uuid AS $$
        DECLARE
            draft_id      uuid;
            src_questions int;  new_questions int;
            src_options   int;  new_options   int;
        BEGIN
            INSERT INTO questionnaires (nickname, version, derived_from_id, created_by)
            SELECT nickname, new_version, id, actor_id
            FROM questionnaires WHERE id = source_id
            RETURNING id INTO draft_id;

            IF draft_id IS NULL THEN
                RAISE EXCEPTION 'questionnaire % not found', source_id;
            END IF;

            INSERT INTO questions (questionnaire_id, position, category, answer_type, prompt,
                                   help_text, trait, scale_min, scale_max, reverse_scored,
                                   max_selections, created_by)
            SELECT draft_id, position, category, answer_type, prompt,
                   help_text, trait, scale_min, scale_max, reverse_scored,
                   max_selections, actor_id
            FROM questions
            WHERE questionnaire_id = source_id;

            INSERT INTO question_options (question_id, answer_type, position, label,
                                          score_value, trait)
            SELECT new_q.id, o.answer_type, o.position, o.label, o.score_value, o.trait
            FROM question_options o
            JOIN questions old_q ON old_q.id = o.question_id
                                AND old_q.questionnaire_id = source_id
            JOIN questions new_q ON new_q.questionnaire_id = draft_id
                                AND new_q.position = old_q.position;

            SELECT count(*) INTO src_questions FROM questions WHERE questionnaire_id = source_id;
            SELECT count(*) INTO new_questions FROM questions WHERE questionnaire_id = draft_id;

            SELECT count(*) INTO src_options
              FROM question_options o JOIN questions q ON q.id = o.question_id
             WHERE q.questionnaire_id = source_id;
            SELECT count(*) INTO new_options
              FROM question_options o JOIN questions q ON q.id = o.question_id
             WHERE q.questionnaire_id = draft_id;

            IF (new_questions, new_options) IS DISTINCT FROM (src_questions, src_options) THEN
                RAISE EXCEPTION
                    'fork of % copied %/% questions/options, expected %/%',
                    source_id, new_questions, new_options, src_questions, src_options;
            END IF;

            RETURN draft_id;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS fork_questionnaire(uuid, text, uuid);

        DROP TRIGGER IF EXISTS question_options_immutable_after_publish ON question_options;
        DROP TRIGGER IF EXISTS questions_immutable_after_publish        ON questions;
        DROP TRIGGER IF EXISTS questionnaires_immutable_after_publish   ON questionnaires;

        DROP FUNCTION IF EXISTS question_options_reject_published_change();
        DROP FUNCTION IF EXISTS questions_reject_published_change();
        DROP FUNCTION IF EXISTS questionnaires_reject_published_change();

        DROP TABLE IF EXISTS entitlements;
        DROP TABLE IF EXISTS purchases;
        DROP TABLE IF EXISTS discount_codes;
        DROP TABLE IF EXISTS report_career_families;
        DROP TABLE IF EXISTS reports;
        DROP TABLE IF EXISTS career_pay;
        DROP TABLE IF EXISTS career_families;
        DROP TABLE IF EXISTS response_answer_options;
        DROP TABLE IF EXISTS response_answers;
        DROP TABLE IF EXISTS questionnaire_responses;
        DROP TABLE IF EXISTS question_options;
        DROP TABLE IF EXISTS questions;
        DROP TABLE IF EXISTS questionnaires;
        DROP TABLE IF EXISTS admin_accounts;
        DROP TABLE IF EXISTS users;

        DROP TYPE IF EXISTS admin_permission;
        DROP TYPE IF EXISTS entitlement_kind;
        DROP TYPE IF EXISTS report_tier;
        DROP TYPE IF EXISTS holland_trait;
        DROP TYPE IF EXISTS answer_type;
        DROP TYPE IF EXISTS question_category;
    """)
