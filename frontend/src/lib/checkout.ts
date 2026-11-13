import type { CheckoutStatus, Questionnaire, Report } from "../api/types";
import { SECTION_ORDER } from "./sections";

/** A report can exist before every section is complete, including while results are pending. */
export function checkoutNextStep(
  status: Pick<CheckoutStatus, "entitled" | "response_id"> | undefined,
  questionnaire: Pick<Questionnaire, "completed_sections"> | undefined,
  report: Pick<Report, "id"> | undefined,
): { kind: "continue"; responseId: string } | { kind: "report"; reportId: string } | null {
  if (!status?.entitled || !status.response_id || !questionnaire) return null;
  if (SECTION_ORDER.some(section => !questionnaire.completed_sections.includes(section))) {
    return { kind: "continue", responseId: status.response_id };
  }
  return report ? { kind: "report", reportId: report.id } : null;
}
