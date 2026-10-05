import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { api, type Run, type RunResults, type TargetSummary, type TaskEntry } from "@/lib/api";
import { cn, fmtCost, fmtScore, fmtSeconds, fmtTime } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EChart } from "@/components/EChart";

const STATUS_BADGE: Record<string, { label: string; variant: "secondary" | "success" | "destructive" | "warning" }> = {
  pending: { label: "等待中", variant: "secondary" },
  running: { label: "运行中", variant: "warning" },
  completed: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "destructive" },
};

const targetColumnHelper = createColumnHelper<TargetSummary>();

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [results, setResults] = useState<RunResults | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      const [r, res] = await Promise.all([
        api.get<Run>(`/api/runs/${runId}`),
        api.get<RunResults>(`/api/runs/${runId}/results`),
      ]);
      setRun(r);
      setResults(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [runId]);

  // 运行中轮询
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      if (!run || run.status === "pending" || run.status === "running") void refresh();
    }, 1000);
    return () => clearInterval(timer);
  }, [refresh, run?.status]);

  const targetColumns = useMemo(
    () => [
      targetColumnHelper.accessor("label", { header: "Evaluation Target" }),
      targetColumnHelper.accessor((r) => `${r.completed_count}/${r.task_count}`, { id: "progress", header: "完成" }),
      targetColumnHelper.accessor("score_mean", {
        header: "平均分",
        cell: (c) => <span className="font-semibold">{fmtScore(c.getValue())}</span>,
      }),
      targetColumnHelper.accessor("total_cost", { header: "Solver 成本", cell: (c) => fmtCost(c.getValue()) }),
      targetColumnHelper.accessor("latency_mean_s", { header: "平均延迟", cell: (c) => fmtSeconds(c.getValue()) }),
    ],
    []
  );

  const targetTable = useReactTable({
    data: results?.targets ?? [],
    columns: targetColumns,
    getCoreRowModel: getCoreRowModel(),
  });

  const scatterOption = useMemo(() => {
    const targets = results?.targets ?? [];
    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item" as const,
        formatter: (p: unknown) => {
          const d = p as { value: [number, number]; name: string };
          return `${d.name}<br/>成本: ${fmtCost(d.value[0])}<br/>得分: ${fmtScore(d.value[1])}`;
        },
      },
      xAxis: { type: "value" as const, name: "Solver 成本 ($)", scale: true },
      yAxis: { type: "value" as const, name: "平均分", max: 100 },
      series: [
        {
          type: "scatter" as const,
          symbolSize: 16,
          data: targets
            .filter((t) => t.score_mean !== null && t.total_cost !== null)
            .map((t) => ({ value: [t.total_cost, t.score_mean], name: t.label })),
          label: { show: true, formatter: "{b}", position: "top" as const, fontSize: 10 },
        },
      ],
    };
  }, [results]);

  async function rejudge() {
    if (!runId) return;
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/rejudge`, {});
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!run) return <p className="text-muted-foreground">加载中…</p>;

  const status = STATUS_BADGE[run.status] ?? STATUS_BADGE.pending;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">{run.name}</h1>
          <p className="text-xs text-muted-foreground">
            {run.dataset_id} v{run.dataset_version}（{run.dataset_revision}）· suite {run.suite_id} · framework{" "}
            {run.framework_version}@{run.framework_commit.slice(0, 8)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={status.variant}>{status.label}</Badge>
          <Button variant="outline" onClick={rejudge} disabled={run.status !== "completed"}>
            重新评分
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="总成本" value={fmtCost(run.total_cost)} sub={`solver ${fmtCost(run.solver_cost)} + judge ${fmtCost(run.judge_cost)}`} />
        <StatCard title="总耗时" value={fmtSeconds(run.total_wall_time_s)} sub={`solver ${fmtSeconds(run.solver_wall_time_s)} + judge ${fmtSeconds(run.judge_wall_time_s)}`} />
        <StatCard title="Solver 执行" value={String(run.solver_execution_count)} sub={fmtTime(run.created_at)} />
        <StatCard title="Judge 执行" value={String(run.judge_execution_count)} sub={`manifest ${run.manifest_hash.slice(0, 12)}…`} />
      </div>

      {run.error && <p className="text-sm text-destructive">{run.error}</p>}

      <Card>
        <CardHeader>
          <CardTitle>Evaluation Target 汇总</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              {targetTable.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  {hg.headers.map((h) => (
                    <TableHead key={h.id}>{flexRender(h.column.columnDef.header, h.getContext())}</TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {targetTable.getRowModel().rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {results && results.targets.length > 1 && (
        <Card>
          <CardHeader>
            <CardTitle>Score vs Cost</CardTitle>
          </CardHeader>
          <CardContent>
            <EChart option={scatterOption} height={300} />
          </CardContent>
        </Card>
      )}

      {results?.targets.map((target) => {
        const key = `${target.deployment_id}:${target.reasoning_profile_id ?? ""}`;
        const entries = results.entries_by_target[key] ?? [];
        return (
          <Card key={key}>
            <CardHeader>
              <CardTitle className="text-base">{target.label}：逐题明细</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {entries.map((entry) => (
                <TaskEntryCard
                  key={entry.solver_execution_id}
                  entry={entry}
                  expanded={expanded === entry.solver_execution_id}
                  onToggle={() =>
                    setExpanded(expanded === entry.solver_execution_id ? null : entry.solver_execution_id)
                  }
                />
              ))}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function StatCard({ title, value, sub }: { title: string; value: string; sub?: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function TaskEntryCard({ entry, expanded, onToggle }: { entry: TaskEntry; expanded: boolean; onToggle: () => void }) {
  const summary = entry.judge_score_summary;
  return (
    <div className="rounded border">
      <button type="button" onClick={onToggle} className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm">
        <span className="font-mono text-xs">{entry.task_id}</span>
        <Badge variant={entry.status === "completed" ? "success" : "destructive"}>{entry.status}</Badge>
        {summary.high_disagreement && <Badge variant="warning">Judge 分歧大</Badge>}
        <span className="ml-auto text-muted-foreground">
          均分 {fmtScore(summary.mean)} · σ {summary.stddev !== null ? summary.stddev.toFixed(1) : "—"} · 延迟{" "}
          {fmtSeconds(entry.total_latency_s)} · {fmtCost(entry.cost)}
        </span>
      </button>
      {expanded && (
        <div className="space-y-3 border-t px-3 py-3">
          {entry.error && <p className="text-sm text-destructive">{entry.error}</p>}
          {entry.response_text && (
            <div>
              <div className="mb-1 text-xs font-medium text-muted-foreground">Solver 回答</div>
              <pre className="whitespace-pre-wrap rounded bg-muted p-3 text-xs">{entry.response_text}</pre>
            </div>
          )}
          {entry.judges.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-muted-foreground">Judge 评分（{entry.judges.length}）</div>
              <div className="space-y-1">
                {entry.judges.map((j) => (
                  <div key={j.judge_execution_id} className="flex flex-wrap items-center gap-2 rounded bg-muted/50 px-3 py-1.5 text-xs">
                    <span className={cn("font-medium", !j.parse_ok && "text-destructive")}>{j.judge_label}</span>
                    <span>总分 {fmtScore(j.weighted_total)}</span>
                    {j.fatal_error && <Badge variant="destructive">致命错误</Badge>}
                    {j.dimension_scores &&
                      Object.entries(j.dimension_scores).map(([dim, score]) => (
                        <span key={dim} className="text-muted-foreground">
                          {dim}: {score}
                        </span>
                      ))}
                    {j.summary && <span className="w-full text-muted-foreground">{j.summary}</span>}
                    {j.error && <span className="w-full text-destructive">{j.error}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
