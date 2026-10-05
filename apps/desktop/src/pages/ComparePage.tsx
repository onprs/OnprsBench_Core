import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ComparabilityVerdict, type Run, type RunResults } from "@/lib/api";
import { fmtCost, fmtScore, fmtSeconds, fmtTime } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HelpTip } from "@/components/HelpTip";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EChart } from "@/components/EChart";

interface CompareRow {
  runId: string;
  runName: string;
  runAt: string;
  label: string;
  score: number | null;
  cost: number | null;
  latency: number | null;
}

/** Pareto 前沿（最大化 score，最小化 cost） */
function paretoFrontier(rows: CompareRow[]): [number, number][] {
  const sorted = rows
    .filter((r) => r.score !== null && r.cost !== null)
    .map((r) => ({ x: r.cost as number, y: r.score as number }))
    .sort((a, b) => a.x - b.x || b.y - a.y);
  const frontier: [number, number][] = [];
  let best = -Infinity;
  for (const p of sorted) {
    if (p.y > best) {
      frontier.push([p.x, p.y]);
      best = p.y;
    }
  }
  return frontier;
}

export function ComparePage() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [verdict, setVerdict] = useState<ComparabilityVerdict | null>(null);
  const [rows, setRows] = useState<CompareRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api.get<Run[]>("/api/runs").then((r) => setRuns(r.filter((x) => x.status === "completed")));
  }, []);

  const compare = useCallback(async () => {
    setError(null);
    setVerdict(null);
    const ids = [...selected];
    try {
      const [v, ...results] = await Promise.all([
        api.get<ComparabilityVerdict>(`/api/runs/compare?ids=${ids.join(",")}`),
        ...ids.map((id) => api.get<RunResults>(`/api/runs/${id}/results`)),
      ]);
      setVerdict(v);
      const runById = new Map(runs.map((r) => [r.id, r]));
      const nextRows: CompareRow[] = [];
      results.forEach((res, idx) => {
        const run = runById.get(ids[idx]);
        for (const t of res.targets) {
          nextRows.push({
            runId: ids[idx],
            runName: run?.name ?? ids[idx],
            runAt: run?.created_at ?? "",
            label: t.label,
            score: t.score_mean,
            cost: t.total_cost,
            latency: t.latency_mean_s,
          });
        }
      });
      setRows(nextRows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [selected, runs]);

  const scatterOption = useMemo(() => {
    const byKey = new Map<string, CompareRow[]>();
    for (const r of rows) {
      const key = `${r.label} @ ${r.runName}`;
      byKey.set(key, [...(byKey.get(key) ?? []), r]);
    }
    const frontier = paretoFrontier(rows);
    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item" as const,
        formatter: (p: unknown) => {
          const d = p as { seriesName: string; value: [number, number] };
          return `${d.seriesName}<br/>成本: ${fmtCost(d.value[0])}<br/>得分: ${fmtScore(d.value[1])}`;
        },
      },
      legend: { type: "scroll" as const, top: 0 },
      xAxis: { type: "log" as const, name: "Solver 成本 ($)" },
      yAxis: { type: "value" as const, name: "平均分", max: 100 },
      series: [
        ...[...byKey.entries()].map(([name, data]) => ({
          name,
          type: "scatter" as const,
          symbolSize: 14,
          data: data
            .filter((r) => r.score !== null && r.cost !== null)
            .map((r) => [Math.max(r.cost as number, 1e-8), r.score as number]),
        })),
        ...(frontier.length >= 2
          ? [
              {
                name: "Pareto 前沿",
                type: "line" as const,
                data: frontier.map(([x, y]) => [Math.max(x, 1e-8), y]),
                showSymbol: true,
                lineStyle: { type: "dashed" as const, width: 2 },
                itemStyle: { color: "#f59e0b" },
              },
            ]
          : []),
      ],
    };
  }, [rows]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">对比</h1>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            选择要对比的 Run
            <HelpTip text="跨 Run 比较前会判定可比性：数据集版本、修订、task revision、框架版本须一致" />
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {runs.length === 0 && <p className="text-sm text-muted-foreground">暂无已完成的 Run</p>}
          {runs.map((run) => (
            <label key={run.id} className="flex cursor-pointer items-center gap-2 rounded border px-3 py-2 text-sm">
              <Checkbox
                checked={selected.has(run.id)}
                onCheckedChange={() => {
                  const next = new Set(selected);
                  if (next.has(run.id)) next.delete(run.id);
                  else next.add(run.id);
                  setSelected(next);
                }}
              />
              <span className="font-medium">{run.name}</span>
              <span className="font-mono text-xs text-muted-foreground">
                {run.dataset_id}@{run.dataset_version}/{run.suite_id}
              </span>
              <span className="ml-auto text-xs text-muted-foreground">{fmtTime(run.created_at)}</span>
            </label>
          ))}
          <Button onClick={compare} disabled={selected.size < 2}>
            对比（已选 {selected.size}）
          </Button>
        </CardContent>
      </Card>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {verdict && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              可比性判定
              <Badge variant={verdict.comparable ? "success" : "warning"}>
                {verdict.verdict ?? (verdict.comparable ? "Comparable" : "Not Directly Comparable")}
              </Badge>
            </CardTitle>
          </CardHeader>
          {verdict.reasons.length > 0 && (
            <CardContent>
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {verdict.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </CardContent>
          )}
        </Card>
      )}

      {rows.length > 0 && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Score vs Cost（含 Pareto 前沿）</CardTitle>
            </CardHeader>
            <CardContent>
              <EChart option={scatterOption} height={380} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>对比表</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Run</TableHead>
                    <TableHead>Evaluation Target</TableHead>
                    <TableHead>平均分</TableHead>
                    <TableHead>Solver 成本</TableHead>
                    <TableHead>平均延迟</TableHead>
                    <TableHead>时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r, i) => (
                    <TableRow key={`${r.runId}-${r.label}-${i}`}>
                      <TableCell>{r.runName}</TableCell>
                      <TableCell>{r.label}</TableCell>
                      <TableCell className="font-semibold">{fmtScore(r.score)}</TableCell>
                      <TableCell>{fmtCost(r.cost)}</TableCell>
                      <TableCell>{fmtSeconds(r.latency)}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{fmtTime(r.runAt)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
