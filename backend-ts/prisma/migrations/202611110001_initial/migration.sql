-- CreateSchema
CREATE SCHEMA IF NOT EXISTS "public";

-- CreateEnum
CREATE TYPE "Stage" AS ENUM ('interests', 'strengths', 'eq');

-- CreateEnum
CREATE TYPE "AnswerType" AS ENUM ('single_choice', 'multi_choice', 'scale', 'free_text');

-- CreateEnum
CREATE TYPE "ReportStatus" AS ENUM ('pending', 'ready');

-- CreateTable
CREATE TABLE "users" (
    "id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "google_sub" TEXT,
    "role" TEXT NOT NULL DEFAULT 'student',
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "questions" (
    "id" UUID NOT NULL,
    "position" INTEGER NOT NULL,
    "category" "Stage" NOT NULL,
    "answer_type" "AnswerType" NOT NULL,
    "prompt" TEXT NOT NULL,
    "help_text" TEXT,
    "scale_min" INTEGER,
    "scale_max" INTEGER,
    "max_selections" INTEGER,

    CONSTRAINT "questions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "question_options" (
    "id" UUID NOT NULL,
    "question_id" UUID NOT NULL,
    "position" INTEGER NOT NULL,
    "label" TEXT NOT NULL,

    CONSTRAINT "question_options_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "attempts" (
    "id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "interests_completed_at" TIMESTAMPTZ,
    "strengths_completed_at" TIMESTAMPTZ,
    "eq_completed_at" TIMESTAMPTZ,
    "questionnaire_snapshot" JSONB NOT NULL,

    CONSTRAINT "attempts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "answers" (
    "id" UUID NOT NULL,
    "attempt_id" UUID NOT NULL,
    "question_id" UUID NOT NULL,
    "numeric" INTEGER,
    "text" TEXT,
    "snapshot" JSONB NOT NULL,
    "submitted_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "answers_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "answer_choices" (
    "answer_id" UUID NOT NULL,
    "question_id" UUID NOT NULL,
    "option_id" UUID NOT NULL,

    CONSTRAINT "answer_choices_pkey" PRIMARY KEY ("answer_id","option_id")
);

-- CreateTable
CREATE TABLE "reports" (
    "id" UUID NOT NULL,
    "attempt_id" UUID NOT NULL,
    "stage" "Stage" NOT NULL,
    "status" "ReportStatus" NOT NULL DEFAULT 'pending',
    "input_snapshot" JSONB NOT NULL,
    "submission_hash" TEXT NOT NULL,
    "input_hash" TEXT NOT NULL,
    "format_version" INTEGER NOT NULL DEFAULT 1,
    "payload" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "generated_at" TIMESTAMPTZ,

    CONSTRAINT "reports_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "purchases" (
    "id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "attempt_id" UUID NOT NULL,
    "stripe_checkout_session_id" TEXT NOT NULL,
    "stripe_payment_intent_id" TEXT,
    "stripe_price_id" TEXT NOT NULL,
    "amount_cents" INTEGER NOT NULL,
    "currency" CHAR(3) NOT NULL,
    "paid_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "refunded_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "purchases_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "entitlements" (
    "id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "attempt_id" UUID NOT NULL,
    "purchase_id" UUID NOT NULL,
    "valid_from" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "revoked_at" TIMESTAMPTZ,

    CONSTRAINT "entitlements_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "discount_codes" (
    "id" UUID NOT NULL,
    "code" TEXT NOT NULL,
    "stripe_coupon_id" TEXT,
    "starts_at" TIMESTAMPTZ,
    "ends_at" TIMESTAMPTZ,
    "max_redemptions" INTEGER,

    CONSTRAINT "discount_codes_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "users_google_sub_key" ON "users"("google_sub");

-- CreateIndex
CREATE UNIQUE INDEX "questions_position_key" ON "questions"("position");

-- CreateIndex
CREATE UNIQUE INDEX "question_options_question_id_position_key" ON "question_options"("question_id", "position");

-- CreateIndex
CREATE UNIQUE INDEX "question_options_question_id_id_key" ON "question_options"("question_id", "id");

-- CreateIndex
CREATE INDEX "attempts_user_id_idx" ON "attempts"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "answers_attempt_id_question_id_key" ON "answers"("attempt_id", "question_id");

-- CreateIndex
CREATE UNIQUE INDEX "answers_id_question_id_key" ON "answers"("id", "question_id");

-- CreateIndex
CREATE UNIQUE INDEX "reports_attempt_id_stage_key" ON "reports"("attempt_id", "stage");

-- CreateIndex
CREATE UNIQUE INDEX "purchases_stripe_checkout_session_id_key" ON "purchases"("stripe_checkout_session_id");

-- CreateIndex
CREATE UNIQUE INDEX "purchases_stripe_payment_intent_id_key" ON "purchases"("stripe_payment_intent_id");

-- CreateIndex
CREATE UNIQUE INDEX "entitlements_purchase_id_key" ON "entitlements"("purchase_id");

-- CreateIndex
CREATE INDEX "entitlements_attempt_id_user_id_idx" ON "entitlements"("attempt_id", "user_id");

-- CreateIndex
CREATE UNIQUE INDEX "discount_codes_code_key" ON "discount_codes"("code");

-- AddForeignKey
ALTER TABLE "question_options" ADD CONSTRAINT "question_options_question_id_fkey" FOREIGN KEY ("question_id") REFERENCES "questions"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "attempts" ADD CONSTRAINT "attempts_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "answers" ADD CONSTRAINT "answers_attempt_id_fkey" FOREIGN KEY ("attempt_id") REFERENCES "attempts"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "answers" ADD CONSTRAINT "answers_question_id_fkey" FOREIGN KEY ("question_id") REFERENCES "questions"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "answer_choices" ADD CONSTRAINT "answer_choices_answer_id_question_id_fkey" FOREIGN KEY ("answer_id", "question_id") REFERENCES "answers"("id", "question_id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "answer_choices" ADD CONSTRAINT "answer_choices_question_id_option_id_fkey" FOREIGN KEY ("question_id", "option_id") REFERENCES "question_options"("question_id", "id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "reports" ADD CONSTRAINT "reports_attempt_id_fkey" FOREIGN KEY ("attempt_id") REFERENCES "attempts"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "purchases" ADD CONSTRAINT "purchases_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "purchases" ADD CONSTRAINT "purchases_attempt_id_fkey" FOREIGN KEY ("attempt_id") REFERENCES "attempts"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "entitlements" ADD CONSTRAINT "entitlements_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "entitlements" ADD CONSTRAINT "entitlements_attempt_id_fkey" FOREIGN KEY ("attempt_id") REFERENCES "attempts"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "entitlements" ADD CONSTRAINT "entitlements_purchase_id_fkey" FOREIGN KEY ("purchase_id") REFERENCES "purchases"("id") ON DELETE RESTRICT ON UPDATE CASCADE;


-- Integrity rules not expressible by the Prisma schema.
ALTER TABLE questions ADD CONSTRAINT question_bounds CHECK (
  (answer_type = 'scale' AND scale_min IS NOT NULL AND scale_max IS NOT NULL AND scale_min < scale_max)
  OR (answer_type <> 'scale' AND scale_min IS NULL AND scale_max IS NULL)
);
ALTER TABLE questions ADD CONSTRAINT question_selection_limit CHECK (
  max_selections IS NULL OR (answer_type = 'multi_choice' AND max_selections > 0)
);
ALTER TABLE attempts ADD CONSTRAINT stage_order CHECK (
  (strengths_completed_at IS NULL OR interests_completed_at IS NOT NULL)
  AND (eq_completed_at IS NULL OR strengths_completed_at IS NOT NULL)
);
ALTER TABLE reports ADD CONSTRAINT report_result_state CHECK (
  (status = 'pending' AND payload IS NULL AND generated_at IS NULL)
  OR (status = 'ready' AND payload IS NOT NULL AND generated_at IS NOT NULL)
);
ALTER TABLE purchases ADD CONSTRAINT purchase_amount CHECK (amount_cents >= 0);
ALTER TABLE answers ADD CONSTRAINT answer_shape CHECK (numeric IS NULL OR text IS NULL);
CREATE FUNCTION immutable_submitted_answer() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Submitted answers are immutable'; END;
$$;
CREATE TRIGGER answers_immutable BEFORE UPDATE OR DELETE ON answers
FOR EACH ROW EXECUTE FUNCTION immutable_submitted_answer();
CREATE TRIGGER answer_choices_immutable BEFORE UPDATE OR DELETE ON answer_choices
FOR EACH ROW EXECUTE FUNCTION immutable_submitted_answer();
