/**
 * 后端 API 客户端与类型定义。
 * Tauri 生产环境下直连 sidecar 端口；Vite 开发环境走 /api 代理。
 */

const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
const API_BASE = isTauri ? "http://127.0.0.1:8765" : "";

export function apiBase(): string {
  return API_BASE;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* 保留 statusText */
    }
    throw new Error(detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

export interface ProviderType {
  type: string;
  label: string;
  base_url: string | null;
  needs_key: boolean;
}

export interface Provider {
  id: string;
  name: string;
  type: string;
  base_url: string | null;
  has_credential: boolean;
  created_at: string;
}

export interface ModelInfo {
  id: string;
  canonical_id: string;
  display_name: string;
  family: string | null;
  generation: string | null;
}

export interface Deployment {
  id: string;
  name: string;
  model_id: string;
  model_display_name: string | null;
  provider_id: string;
  provider_name: string | null;
  provider_type: string | null;
  api_model_name: string;
  endpoint_override: string | null;
  price_input_per_mtok: number | null;
  price_output_per_mtok: number | null;
  created_at: string;
}

export interface ReasoningProfile {
  id: string;
  deployment_id: string;
  name: string;
  reasoning_effort: string | null;
  reasoning_budget: number | null;
  max_output_tokens: number | null;
  temperature: number | null;
  top_p: number | null;
  seed: number | null;
  provider_params: Record<string, unknown>;
}

export interface SuiteInfo {
  id: string;
  name: string;
  task_count: number;
}

export interface DatasetInstallation {
  id: string;
  dataset_id: string;
  dataset_name: string;
  dataset_version: string;
  dataset_revision: string;
  protocol_version: string;
  manifest_hash: string;
  source_path: string;
  installed_at: string;
  suites: SuiteInfo[];
}

export interface DatasetTask {
  id: string;
  task_id: string;
  revision: number;
  type: string;
  tags: string[];
  domains: string[];
  contamination: string;
  freshness: string;
  problem: string;
}

export interface TargetSpecIn {
  deployment_id: string;
  reasoning_profile_id: string | null;
}

export interface Run {
  id: string;
  name: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  framework_version: string;
  framework_commit: string;
  dataset_id: string;
  dataset_version: string;
  dataset_revision: string;
  manifest_hash: string;
  suite_id: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  solver_wall_time_s: number | null;
  judge_wall_time_s: number | null;
  total_wall_time_s: number | null;
  solver_cost: number | null;
  judge_cost: number | null;
  total_cost: number | null;
  error: string | null;
  solver_execution_count: number;
  judge_execution_count: number;
}

export interface JudgeScoreSummary {
  mean: number | null;
  median: number | null;
  stddev: number | null;
  min: number | null;
  max: number | null;
  count: number;
  high_disagreement: boolean;
}

export interface JudgeResult {
  judge_execution_id: string;
  judge_label: string;
  status: string;
  parse_ok: boolean;
  fatal_error: boolean | null;
  dimension_scores: Record<string, number> | null;
  weighted_total: number | null;
  summary: string | null;
  key_errors: string[] | null;
  total_latency_s: number | null;
  cost: number | null;
  error: string | null;
}

export interface TaskEntry {
  solver_execution_id: string;
  task_id: string;
  task_revision: number;
  status: string;
  response_text: string | null;
  error: string | null;
  total_latency_s: number | null;
  cost: number | null;
  judge_score_summary: JudgeScoreSummary;
  judges: JudgeResult[];
}

export interface TargetSummary {
  deployment_id: string;
  reasoning_profile_id: string | null;
  label: string;
  task_count: number;
  completed_count: number;
  score_mean: number | null;
  total_cost: number | null;
  latency_mean_s: number | null;
}

export interface RunResults {
  run_id: string;
  status: string;
  targets: TargetSummary[];
  entries_by_target: Record<string, TaskEntry[]>;
  wall_time: { solver_s: number | null; judge_s: number | null; total_s: number | null };
  cost: { solver: number | null; judge: number | null; total: number | null };
}

export interface ComparabilityVerdict {
  comparable: boolean;
  verdict?: string;
  reasons: string[];
}

export interface TimeseriesPoint {
  run_id: string;
  run_name: string;
  run_at: string;
  dataset_id: string;
  suite_id: string;
  deployment_id: string;
  reasoning_profile_id: string | null;
  label: string;
  score_mean: number | null;
  total_cost: number | null;
  latency_mean_s: number | null;
}
