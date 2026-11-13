/**
 * The controller layer for server state.
 *
 * TanStack Query owns caching, loading and error state; components consume these hooks and
 * never see a request. Keeping the query keys here — rather than inline at call sites — means
 * invalidation is something you can reason about in one place.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import { useQuestionnaireStore } from "../store/questionnaire";
import type { AnswerInput, QuestionCategory, StartRequest } from "./types";

export const queryKeys = {
  questionnaire: (responseId?: string) => ["questionnaire", responseId ?? null] as const,
  report: (reportId: string) => ["report", reportId] as const,
  latestReport: (responseId: string) => ["latest-report", responseId] as const,
  me: ["me"] as const,
  pricing: ["pricing"] as const,
  checkoutStatus: (sessionId: string) => ["checkout", sessionId] as const,
};

export function useQuestionnaire(responseId?: string) {
  return useQuery({
    queryKey: queryKeys.questionnaire(responseId),
    queryFn: () => api.questionnaire(responseId),
    // The *questions* are immutable once published, but the *sections* depend on the
    // entitlement, and so does the record of which sections are done. Both change over the
    // life of a response — after a purchase, and after each submission — so this refetches
    // rather than serving a cached answer to a question that has moved.
    staleTime: 0,
    retry: false,
  });
}

export function useStartResponse() {
  return useMutation({
    mutationFn: (body: StartRequest) => api.start(body),
  });
}

export function useSubmitSection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      responseId,
      section,
      answers,
    }: {
      responseId: string;
      section: QuestionCategory;
      answers: AnswerInput[];
    }) => api.submitSection(responseId, section, answers),
    onSuccess: (_result, variables) => {
      // A submitted section is now done, and the server won't accept it again. The
      // questionnaire carries that list, so it has to be refetched — otherwise the client
      // would offer a section the server refuses.
      queryClient.invalidateQueries({
        queryKey: queryKeys.questionnaire(variables.responseId),
      });
    },
  });
}

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: api.me,
    // Sign-in happens through a full-page redirect, so the app remounts and refetches anyway.
    // What this guards against is a stale "signed out" after returning from Google.
    staleTime: 0,
    retry: false,
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      useQuestionnaireStore.getState().reset();
      queryClient.clear();
    },
  });
}

export function useReport(reportId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.report(reportId ?? ""),
    queryFn: () => api.report(reportId!),
    enabled: Boolean(reportId),
    staleTime: 0,
    refetchInterval: (query) => query.state.data?.status === "pending" ? 5000 : false,
  });
}

/**
 * The newest report for a response.
 *
 * Used after a purchase, when the browser has a response id but no report id.
 */
export function useLatestReport(responseId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.latestReport(responseId ?? ""),
    queryFn: () => api.latestReport(responseId!),
    enabled: Boolean(responseId),
    retry: false,
    // The report is append-only, but a *newer* one can appear when the paid sections are
    // answered, so this one is refetched rather than cached forever.
    staleTime: 0,
  });
}

// ---------------------------------------------------------------- payments

export function usePricing() {
  return useQuery({
    queryKey: queryKeys.pricing,
    queryFn: api.pricing,
    // The price is a deployment setting, so it changes when the app is redeployed — not
    // while someone is reading it.
    staleTime: Infinity,
    retry: false,
  });
}

export function useCreateCheckout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ responseId, discountCode }: { responseId: string; discountCode?: string }) =>
      api.createCheckout(responseId, discountCode),
    onSuccess: () => {
      // The entitlement may now exist, so the sections this visitor may answer have changed.
      queryClient.invalidateQueries({ queryKey: ["questionnaire"] });
    },
  });
}

export function useApplyDiscount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ responseId, discountCode }: { responseId: string; discountCode: string }) =>
      api.applyDiscount(responseId, discountCode),
    onSuccess: () => {
      // After applying a discount, entitlement may be granted; refresh questionnaire.
      queryClient.invalidateQueries({ queryKey: ["questionnaire"] });
    },
  });
}
/**
 * Whether a checkout has resulted in access.
 *
 * The webhook that grants access may not have landed when the browser comes back, so the
 * return page asks the server — which verifies the session with Stripe and, if necessary,
 * performs the grant itself. `refetchInterval` covers the case where even that is a moment
 * away.
 */
export function useCheckoutStatus(sessionId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.checkoutStatus(sessionId ?? ""),
    queryFn: () => api.checkoutStatus(sessionId!),
    enabled: Boolean(sessionId),
    retry: false,
    // Polled only while the answer is still "not yet" — a paid session resolves at once, and
    // an unpaid one never will.
    refetchInterval: (query) => {
      const status = query.state.data;
      return status?.payment_status === "pending" ? 3000 : false;
    },
  });
}
