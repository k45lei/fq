-- Allow direct (non-purchase) entitlements granted by 100% discount codes, and track redemptions.
ALTER TABLE "discount_codes" ADD COLUMN "redemptions_count" INTEGER NOT NULL DEFAULT 0;

ALTER TABLE "entitlements" ALTER COLUMN "purchase_id" DROP NOT NULL;
ALTER TABLE "entitlements" ADD COLUMN "discount_code_id" UUID;

CREATE INDEX "entitlements_discount_code_id_idx" ON "entitlements"("discount_code_id");

ALTER TABLE "entitlements" ADD CONSTRAINT "entitlements_discount_code_id_fkey" FOREIGN KEY ("discount_code_id") REFERENCES "discount_codes"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
