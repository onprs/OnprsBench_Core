import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api,
  type DatasetInstallation,
  type Deployment,
  type ReasoningProfile,
  type TargetSpecIn,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HelpTip } from "@/components/HelpTip";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectEmpty, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

interface TargetOption {
  key: string; // deployment_id:profile_id
  deployment_id: string;
  reasoning_profile_id: string | null;
  label: string;
}

function targetKey(deploymentId: string, profileId: string | null): string {
  return `${deploymentId}:${profileId ?? ""}`;
}

function buildTargetOptions(deployments: Deployment[], profiles: ReasoningProfile[]): TargetOption[] {
  const options: TargetOption[] = [];
  for (const d of deployments) {
    options.push({
      key: targetKey(d.id, null),
      deployment_id: d.id,
      reasoning_profile_id: null,
      label: d.name,
    });
    for (const p of profiles.filter((p) => p.deployment_id === d.id)) {
      options.push({
        key: targetKey(d.id, p.id),
        deployment_id: d.id,
        reasoning_profile_id: p.id,
        label: `${d.name} · ${p.name}`,
      });
    }
  }
  return options;
}

function TargetPicker({
  title,
  tip,
  options,
  selected,
  onToggle,
}: {
  title: string;
  tip: string;
  options: TargetOption[];
  selected: Set<string>;
  onToggle: (key: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          {title}
          <HelpTip text={tip} />
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {options.length === 0 && <p className="text-sm text-muted-foreground">请先在「设置」中创建 Deployment</p>}
        {options.map((opt) => (
          <label key={opt.key} className="flex cursor-pointer items-center gap-2 rounded border px-3 py-2 text-sm">
            <Checkbox checked={selected.has(opt.key)} onCheckedChange={() => onToggle(opt.key)} />
            <span>{opt.label}</span>
          </label>
        ))}
      </CardContent>
    </Card>
  );
}

export function NewRunPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [installations, setInstallations] = useState<DatasetInstallation[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [profiles, setProfiles] = useState<ReasoningProfile[]>([]);
  const [installationId, setInstallationId] = useState("");
  const [suiteId, setSuiteId] = useState("");
  const [solverKeys, setSolverKeys] = useState<Set<string>>(new Set());
  const [judgeKeys, setJudgeKeys] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    void (async () => {
      const [insts, deps, profs] = await Promise.all([
        api.get<DatasetInstallation[]>("/api/datasets/installations"),
        api.get<Deployment[]>("/api/deployments"),
        api.get<ReasoningProfile[]>("/api/reasoning-profiles"),
      ]);
      setInstallations(insts);
      setDeployments(deps);
      setProfiles(profs);
    })();
  }, []);

  const options = useMemo(() => buildTargetOptions(deployments, profiles), [deployments, profiles]);
  const selectedInstallation = installations.find((i) => i.id === installationId);
  const selectedSuite = selectedInstallation?.suites.find((s) => s.id === suiteId);

  const toggle = useCallback((set: Set<string>, key: string, apply: (s: Set<string>) => void) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    apply(next);
  }, []);

  function toTargets(keys: Set<string>): TargetSpecIn[] {
    return [...keys].map((key) => {
      const opt = options.find((o) => o.key === key)!;
      return { deployment_id: opt.deployment_id, reasoning_profile_id: opt.reasoning_profile_id };
    });
  }

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const run = await api.post<{ id: string }>("/api/runs", {
        name: name || `Run ${new Date().toLocaleString("zh-CN", { hour12: false })}`,
        installation_id: installationId,
        suite_id: suiteId,
        solvers: toTargets(solverKeys),
        judges: toTargets(judgeKeys),
      });
      navigate(`/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  const canSubmit =
    installationId &&
    suiteId &&
    (selectedSuite?.task_count ?? 0) > 0 &&
    solverKeys.size > 0 &&
    judgeKeys.size > 0 &&
    !submitting;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">新建 Run</h1>

      <Card>
        <CardHeader>
          <CardTitle>基本信息</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 lg:grid-cols-3">
          <div className="space-y-1">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="可选" />
          </div>
          <div className="space-y-1">
            <Label>数据集</Label>
            <Select value={installationId} onValueChange={(v) => { setInstallationId(v); setSuiteId(""); }}>
              <SelectTrigger>
                <SelectValue placeholder="选择已安装数据集" />
              </SelectTrigger>
              <SelectContent>
                {installations.length === 0 && (
                  <SelectEmpty>暂无已安装数据集（请先在「数据集」页安装）</SelectEmpty>
                )}
                {installations.map((i) => (
                  <SelectItem key={i.id} value={i.id}>
                    {i.dataset_name} v{i.dataset_version}（{i.dataset_revision}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Suite</Label>
            <Select value={suiteId} onValueChange={setSuiteId}>
              <SelectTrigger>
                <SelectValue placeholder="选择 suite" />
              </SelectTrigger>
              <SelectContent>
                {!selectedInstallation && <SelectEmpty>请先选择数据集</SelectEmpty>}
                {selectedInstallation && selectedInstallation.suites.length === 0 && (
                  <SelectEmpty>该数据集没有 suite</SelectEmpty>
                )}
                {selectedInstallation?.suites.map((s) => (
                  <SelectItem key={s.id} value={s.id} disabled={s.task_count === 0}>
                    {s.name}（{s.task_count} 题{s.task_count === 0 ? "，不可运行" : ""}）
                  </SelectItem>
                ))}
                {selectedInstallation &&
                  selectedInstallation.suites.length > 0 &&
                  selectedInstallation.suites.every((s) => s.task_count === 0) && (
                    <SelectEmpty>该数据集的 suite 暂无可运行任务</SelectEmpty>
                  )}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <TargetPicker
          title="Solver"
          tip="被评测的 Evaluation Target（Deployment × Reasoning Profile），可多选"
          options={options}
          selected={solverKeys}
          onToggle={(k) => toggle(solverKeys, k, setSolverKeys)}
        />
        <TargetPicker
          title="Judge"
          tip="评分者，并行运行；输入中对 Solver 身份匿名化"
          options={options}
          selected={judgeKeys}
          onToggle={(k) => toggle(judgeKeys, k, setJudgeKeys)}
        />
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button size="lg" onClick={submit} disabled={!canSubmit}>
        {submitting ? "创建中…" : "开始运行"}
      </Button>
    </div>
  );
}
