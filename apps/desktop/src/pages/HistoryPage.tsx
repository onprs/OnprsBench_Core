import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { api, type Run, type TimeseriesPoint } from "@/lib/api";
import { fmtCost, fmtSeconds, fmtTime } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HelpTip } from "@/components/HelpTip";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EChart } from "@/components/EChart";

const columnHelper = createColumnHelper<Run>();

const STATUS_LABEL: Record<string, string> = {
  pending: "等待中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function HistoryPage() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<Run[]>([]);
  const [points, setPoints] = useState<TimeseriesPoint[]>([]);

  const reload = useCallback(async () => {
    const [r, ts] = await Promise.all([
      api.get<Run[]>("/api/runs"),
      api.get<TimeseriesPoint[]>("/api/analytics/timeseries"),
    ]);
    setRuns(r);
    setPoints(ts);
  }, []);

  useEffect(() => {
    void reload();
    const timer = setInterval(() => void reload(), 3000);
    return () => clearInterval(timer);
  }, [reload]);

  const columns = useMemo(
    () => [
      columnHelper.accessor("name", { header: "名称" }),
      columnHelper.accessor("status", {
        header: "状态",
        cell: (c) => (
          <Badge variant={c.getValue() === "completed" ? "success" : c.getValue() === "failed" ? "destructive" : "warning"}>
            {STATUS_LABEL[c.getValue()] ?? c.getValue()}
          </Badge>
        ),
      }),
      columnHelper.accessor("dataset_id", {
        header: "数据集",
        cell: (c) => (
          <span className="font-mono text-xs">
            {c.row.original.dataset_id}@{c.row.original.dataset_version}/{c.row.original.suite_id}
          </span>
        ),
      }),
      columnHelper.accessor("solver_execution_count", { header: "Solver" }),
      columnHelper.accessor("judge_execution_count", { header: "Judge" }),
      columnHelper.accessor("total_cost", { header: "总成本", cell: (c) => fmtCost(c.getValue()) }),
      columnHelper.accessor("total_wall_time_s", { header: "总耗时", cell: (c) => fmtSeconds(c.getValue()) }),
      columnHelper.accessor("created_at", { header: "创建时间", cell: (c) => fmtTime(c.getValue()) }),
    ],
    []
  );

  const table = useReactTable({ data: runs, columns, getCoreRowModel: getCoreRowModel() });

  const timeseriesOption = useMemo(() => {
    const byLabel = new Map<string, [string, number][]>();
    for (const p of points) {
      if (p.score_mean === null) continue;
      const list = byLabel.get(p.label) ?? [];
      list.push([p.run_at, p.score_mean]);
      byLabel.set(p.label, list);
    }
    return {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" as const },
      legend: { type: "scroll" as const, top: 0 },
      xAxis: { type: "time" as const },
      yAxis: { type: "value" as const, name: "平均分", max: 100 },
      series: [...byLabel.entries()].map(([label, data]) => ({
        name: label,
        type: "line" as const,
        symbolSize: 8,
        data,
      })),
    };
  }, [points]);

  const costLatencyOption = useMemo(() => {
    const byLabel = new Map<string, { cost: [string, number][]; latency: [string, number][] }>();
    for (const p of points) {
      const entry = byLabel.get(p.label) ?? { cost: [], latency: [] };
      if (p.total_cost !== null) entry.cost.push([p.run_at, p.total_cost]);
      if (p.latency_mean_s !== null) entry.latency.push([p.run_at, p.latency_mean_s]);
      byLabel.set(p.label, entry);
    }
    return {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" as const },
      legend: { type: "scroll" as const, top: 0 },
      xAxis: [{ type: "time" as const, gridIndex: 0 }, { type: "time" as const, gridIndex: 1 }],
      yAxis: [
        { type: "value" as const, gridIndex: 0, name: "成本 ($)" },
        { type: "value" as const, gridIndex: 1, name: "延迟 (s)" },
      ],
      grid: [{ top: 40, height: "32%" }, { top: "58%", height: "32%" }],
      series: [...byLabel.entries()].flatMap(([label, d]) => [
        { name: `${label} 成本`, type: "line" as const, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, data: d.cost },
        { name: `${label} 延迟`, type: "line" as const, xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: d.latency },
      ]),
    };
  }, [points]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">历史</h1>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            Score over Time
            <HelpTip text="同一 Evaluation Target 跨 Run 的得分变化，用于发现能力漂移" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          {points.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无已完成的 Run</p>
          ) : (
            <EChart option={timeseriesOption} height={320} />
          )}
        </CardContent>
      </Card>

      {points.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Cost / Latency over Time</CardTitle>
          </CardHeader>
          <CardContent>
            <EChart option={costLatencyOption} height={440} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>全部 Run</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  {hg.headers.map((h) => (
                    <TableHead key={h.id}>{flexRender(h.column.columnDef.header, h.getContext())}</TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  className="cursor-pointer"
                  onClick={() => navigate(`/runs/${row.original.id}`)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
