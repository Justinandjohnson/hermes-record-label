/**
 * Roundtable verdict — Dez's structured close-of-meeting decision.
 *
 * Shape mirrors the `roundtable_verdicts` table from migration 013.
 * Wiring to the backend lands in Phase 2 (see BUILD_PLAN.md). For now this
 * file defines the contract so frontend code can build against it.
 */

export type VerdictRecommendation =
  | "SHIP"
  | "REVISE"
  | "VAULT"
  | "MINE_FOR_LOOPS";

export type NextActionKind =
  | "approve"
  | "request_revision"
  | "vault"
  | "wave_vault";

/** Payload shape for `wave_vault` — which segments to extract. */
export interface WaveVaultActionPayload {
  segments: Array<{
    stem: "vocals" | "drums" | "bass" | "other" | "full";
    start_sec: number | null;
    end_sec: number | null;
    notes?: string;
  }>;
}

/** Payload for `request_revision` — Dez's specific revision asks. */
export interface RequestRevisionPayload {
  focus_areas: string[];
}

export type NextActionPayload =
  | WaveVaultActionPayload
  | RequestRevisionPayload
  | Record<string, never>;

export interface Verdict {
  id: number;
  track_id: number;
  recommendation: VerdictRecommendation;
  headline: string;
  reasoning: string;
  next_action_kind: NextActionKind;
  next_action_payload: NextActionPayload | null;
  created_at: string;
  superseded_at: string | null;
}

/** Visual treatment for each recommendation. */
export const VERDICT_META: Record<
  VerdictRecommendation,
  { label: string; tone: "positive" | "caution" | "neutral" | "salvage" }
> = {
  SHIP: { label: "Ready to ship", tone: "positive" },
  REVISE: { label: "Needs another pass", tone: "caution" },
  VAULT: { label: "Vault — not landing", tone: "neutral" },
  MINE_FOR_LOOPS: { label: "Mine for loops", tone: "salvage" },
};

/** CTA copy + intent per next action. */
export const NEXT_ACTION_META: Record<
  NextActionKind,
  { cta: string; description: string }
> = {
  approve: {
    cta: "Approve and send to Maren",
    description: "Move to ART_NEEDED. Maren takes it from here.",
  },
  request_revision: {
    cta: "Send revision notes",
    description: "Track goes back to the artist with focus areas.",
  },
  vault: {
    cta: "Vault this track",
    description: "Shelved with reasoning preserved. Reversible.",
  },
  wave_vault: {
    cta: "Save the loops and vault the rest",
    description: "Pulls flagged stems into the Wave Vault for future use.",
  },
};
