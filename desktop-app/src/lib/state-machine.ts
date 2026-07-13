export const RELEASE_STATES = [
  "DRAFT",
  "IN_REVIEW",
  "FEEDBACK_GIVEN",
  "APPROVED",
  "ART_NEEDED",
  "ART_SUBMITTED",
  "ART_APPROVED",
  "RELEASE_READY",
  "PREFLIGHT",
  "UPLOADING",
  "RELEASED",
] as const;

export type ReleaseState = (typeof RELEASE_STATES)[number];

export const STATE_LABELS: Record<ReleaseState, string> = {
  DRAFT: "Draft",
  IN_REVIEW: "In Review",
  FEEDBACK_GIVEN: "Feedback",
  APPROVED: "Approved",
  ART_NEEDED: "Art Needed",
  ART_SUBMITTED: "Art Submitted",
  ART_APPROVED: "Art Approved",
  RELEASE_READY: "Ready",
  PREFLIGHT: "Preflight",
  UPLOADING: "Uploading",
  RELEASED: "Released",
};

export const STATE_COLORS: Record<ReleaseState, string> = {
  DRAFT: "bg-zinc-600",
  IN_REVIEW: "bg-yellow-600",
  FEEDBACK_GIVEN: "bg-orange-600",
  APPROVED: "bg-emerald-600",
  ART_NEEDED: "bg-purple-600",
  ART_SUBMITTED: "bg-purple-500",
  ART_APPROVED: "bg-purple-400",
  RELEASE_READY: "bg-blue-600",
  PREFLIGHT: "bg-blue-500",
  UPLOADING: "bg-blue-400",
  RELEASED: "bg-label-500",
};

export const TRANSITIONS: Record<ReleaseState, ReleaseState[]> = {
  DRAFT: ["IN_REVIEW"],
  IN_REVIEW: ["FEEDBACK_GIVEN"],
  FEEDBACK_GIVEN: ["DRAFT", "APPROVED"],
  APPROVED: ["ART_NEEDED"],
  ART_NEEDED: ["ART_SUBMITTED"],
  ART_SUBMITTED: ["ART_NEEDED", "ART_APPROVED"],
  ART_APPROVED: ["RELEASE_READY"],
  RELEASE_READY: ["PREFLIGHT"],
  PREFLIGHT: ["UPLOADING", "ART_NEEDED", "FEEDBACK_GIVEN"],
  UPLOADING: ["RELEASED", "RELEASE_READY"],
  RELEASED: [],
};

export function stateIndex(state: ReleaseState): number {
  return RELEASE_STATES.indexOf(state);
}
