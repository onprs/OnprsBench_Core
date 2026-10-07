/** 协议枚举值的中文展示标签；未知值原样返回，便于对照协议排查。 */

const TASK_TYPE_LABELS: Record<string, string> = {
  code_generation: "竞赛代码",
  issue_resolution: "工程修复",
  implementation: "论文实现",
  algorithm_design: "算法设计",
  academic_qa: "文本问答",
};

const DIFFICULTY_LABELS: Record<string, string> = {
  easy: "简单",
  medium: "中等",
  hard: "困难",
  very_hard: "极难",
  frontier: "前沿",
};

const CONTAMINATION_LABELS: Record<string, string> = {
  none: "无",
  low: "低",
  medium: "中",
  high: "高",
  unknown: "未知",
};

export function labelForTaskType(value: string | null | undefined): string {
  if (!value) return "—";
  return TASK_TYPE_LABELS[value] ?? value;
}

export function labelForDifficulty(value: string | null | undefined): string {
  if (!value) return "—";
  return DIFFICULTY_LABELS[value] ?? value;
}

export function labelForContamination(value: string | null | undefined): string {
  if (!value) return "—";
  return CONTAMINATION_LABELS[value] ?? value;
}

const PRICE_SOURCE_LABELS: Record<string, string> = {
  manual_override: "手动设置",
  models_dev: "models.dev",
  litellm_cost_map: "LiteLLM 价格目录",
  unknown: "未匹配",
};

export function labelForPriceSource(value: string | null | undefined): string {
  if (!value) return "—";
  return PRICE_SOURCE_LABELS[value] ?? value;
}
