"""Helpers for building a small but structurally complete questionnaire in tests.

Deliberately covers every `answer_type`, including the ones with options and the ones without,
so that a fork has something of each kind to get wrong.
"""

from uuid import UUID

from sqlalchemy import Connection, text


def create_admin(conn: Connection, email: str = "admin@example.com") -> UUID:
    return conn.execute(
        text("""
            INSERT INTO admin_accounts (name, email, permissions)
            VALUES ('Test Admin', :email,
                    ARRAY['author_questionnaire', 'publish_questionnaire']::admin_permission[])
            RETURNING id
        """),
        {"email": email},
    ).scalar_one()


def create_questionnaire(conn: Connection, actor_id: UUID, version: str = "v1") -> UUID:
    """A draft questionnaire with one question of each answer_type, plus options."""
    questionnaire_id = conn.execute(
        text("""
            INSERT INTO questionnaires (nickname, version, created_by)
            VALUES ('test-instrument', :version, :actor)
            RETURNING id
        """),
        {"version": version, "actor": actor_id},
    ).scalar_one()

    params = {"q": questionnaire_id, "actor": actor_id}

    multi_id = conn.execute(
        text("""
            INSERT INTO questions (questionnaire_id, position, category, answer_type, prompt,
                                   help_text, max_selections, created_by)
            VALUES (:q, 1, 'interests', 'multi_choice',
                    'Which of these activities appeal to you?',
                    'Pick up to three.', 3, :actor)
            RETURNING id
        """),
        params,
    ).scalar_one()

    for position, (label, trait) in enumerate(
        [
            ("Repairing an engine", "realistic"),
            ("Running an experiment", "investigative"),
            ("Designing a poster", "artistic"),
        ],
        start=1,
    ):
        conn.execute(
            text("""
                INSERT INTO question_options (question_id, answer_type, position, label, trait)
                VALUES (:question, 'multi_choice', :position, :label, CAST(:trait AS holland_trait))
            """),
            {"question": multi_id, "position": position, "label": label, "trait": trait},
        )

    conn.execute(
        text("""
            INSERT INTO questions (questionnaire_id, position, category, answer_type, prompt,
                                   trait, scale_min, scale_max, reverse_scored, created_by)
            VALUES (:q, 2, 'eq', 'scale', 'I stay calm under pressure.',
                    'social', 1, 5, true, :actor)
        """),
        params,
    )

    conn.execute(
        text("""
            INSERT INTO questions (questionnaire_id, position, category, answer_type, prompt,
                                   created_by)
            VALUES (:q, 3, 'strengths', 'free_text',
                    'Describe something you built that you are proud of.', :actor)
        """),
        params,
    )

    single_id = conn.execute(
        text("""
            INSERT INTO questions (questionnaire_id, position, category, answer_type, prompt,
                                   created_by)
            VALUES (:q, 4, 'interests', 'single_choice',
                    'Which sounds more like a good day at work?', :actor)
            RETURNING id
        """),
        params,
    ).scalar_one()

    for position, (label, score) in enumerate(
        [("Solving a problem alone", 1), ("Leading a team through one", 2)], start=1
    ):
        conn.execute(
            text("""
                INSERT INTO question_options (question_id, answer_type, position, label,
                                              score_value)
                VALUES (:question, 'single_choice', :position, :label, :score)
            """),
            {"question": single_id, "position": position, "label": label, "score": score},
        )

    return questionnaire_id


def create_user(conn: Connection, email: str = "student@example.com") -> UUID:
    return conn.execute(
        text("INSERT INTO users (name, email) VALUES ('Test Student', :email) RETURNING id"),
        {"email": email},
    ).scalar_one()


def create_response(conn: Connection, user_id: UUID, questionnaire_id: UUID) -> UUID:
    return conn.execute(
        text("""
            INSERT INTO questionnaire_responses (user_id, questionnaire_id, contact_email)
            SELECT :user, :questionnaire, u.email FROM users u WHERE u.id = :user
            RETURNING id
        """),
        {"user": user_id, "questionnaire": questionnaire_id},
    ).scalar_one()


def create_purchase(conn: Connection, user_id: UUID) -> UUID:
    return conn.execute(
        text("""
            INSERT INTO purchases (user_id, amount_cents, paid_at)
            VALUES (:user, 2900, now())
            RETURNING id
        """),
        {"user": user_id},
    ).scalar_one()


def question_of_type(conn: Connection, questionnaire_id: UUID, answer_type: str) -> UUID:
    return conn.execute(
        text("""
            SELECT id FROM questions
             WHERE questionnaire_id = :q AND answer_type = CAST(:t AS answer_type)
             ORDER BY position LIMIT 1
        """),
        {"q": questionnaire_id, "t": answer_type},
    ).scalar_one()


def publish(conn: Connection, questionnaire_id: UUID, actor_id: UUID) -> None:
    conn.execute(
        text("""
            UPDATE questionnaires
               SET published_at = now(), published_by = :actor
             WHERE id = :q
        """),
        {"q": questionnaire_id, "actor": actor_id},
    )
