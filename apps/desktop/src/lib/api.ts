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
      const payload = data.detail ?? data;
      if (typeof payload === "string") {
        detail = payload;
      } else if (payload && typeof payload === "object") {
        const parts: string[] = [];
        if (typeof payload.message === "string") parts.push(payload.message);
        if (Array.isArray(payload.errors)) parts.push(...payload.errors.map((item: unknown) => String(item)));
        if (typeof payload.hint === "string") parts.push(`提示：${payload.hint}`);
        detail = parts.length > 0 ? parts.join("\n") : JSON.stringify(payload);
      }
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
  /** 已拉取的模型数量与时间（0 表示尚未拉取） */
  model_catalog_count: number;
  model_catalog_fetched_at: string | null;
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
  price_cached_input_per_mtok: number | null;
  price_cache_write_per_mtok: number | null;
  created_at: string;
}

/** 模型能力（来自 models.dev 目录；null 表示目录中没有该模型） */
export interface PricingPreviewCapabilities {
  source: string;
  reasoning: boolean | null;
  tool_call: boolean | null;
  attachment: boolean | null;
  context_limit: number | null;
  output_limit: number | null;
  modalities: unknown;
}

/** 价格与能力预览（部署表单自动填充） */
export interface PricingPreview {
  source: string;
  price_input_per_mtok: number | null;
  price_output_per_mtok: number | null;
  price_cached_input_per_mtok: number | null;
  price_cache_write_per_mtok: number | null;
  capabilities: PricingPreviewCapabilities | null;
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
  /** Agent 最大工具循环轮次：null = 框架默认；0 = 不限制 */
  agent_max_turns: number | null;
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
  /** 分发形态：standard（判定资源按需下载）/ full（附带资源，可离线判定） */
  distribution: "standard" | "full" | string;
  source_path: string;
  installed_at: string;
  suites: SuiteInfo[];
}

export interface DatasetTask {
  id: string;
  task_id: string;
  revision: number;
  task_hash: string;
  title: string;
  type: string;
  status: string;
  tags: string[];
  difficulty: string;
  flagship: boolean;
  contamination: string;
  freshness: string;
  has_verify_contract: boolean;
  problem: string;
  rubric_dimensions: { id: string; weight: number }[];
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
  verifier_wall_time_s: number | null;
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
  /** 实际花费的模型调用轮次 */
  turns: number | null;
  /** 末次调用的结束原因（stop / length / tool_calls） */
  finish_reason: string | null;
  /** 是否发生过输出预算耗尽 */
  truncated: boolean;
  /** oneshot / agent / oneshot_fallback */
  solver_mode: string | null;
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
  /** 花费轮次：平均 / 总计 / 被截断任务数 */
  turns_mean: number | null;
  turns_total: number | null;
  truncated_count: number;
}

export interface RunResults {
  run_id: string;
  status: string;
  targets: TargetSummary[];
  entries_by_target: Record<string, TaskEntry[]>;
  wall_time: { solver_s: number | null; verifier_s: number | null; judge_s: number | null; total_s: number | null };
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
  turns_mean: number | null;
  truncated_count: number;
}
