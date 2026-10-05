import { useCallback, useEffect, useState } from "react";
import { api, type DatasetInstallation, type DatasetTask } from "@/lib/api";
import { fmtTime } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function DatasetsPage() {
  const [path, setPath] = useState("");
  const [installations, setInstallations] = useState<DatasetInstallation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tasks, setTasks] = useState<DatasetTask[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setInstallations(await api.get<DatasetInstallation[]>("/api/datasets/installations"));
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function install() {
    setError(null);
    setInfo(null);
    try {
      const result = await api.post<{ created: boolean; installation: DatasetInstallation }>(
        "/api/datasets/installations",
        { path }
      );
      setInfo(result.created ? "安装成功" : "相同内容的数据集已安装，已复用");
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function showTasks(installationId: string) {
    setSelected(installationId);
    setTasks(await api.get<DatasetTask[]>(`/api/datasets/installations/${installationId}/tasks`));
  }

  async function uninstall(inst: DatasetInstallation) {
    if (!window.confirm(`卸载数据集「${inst.dataset_name} v${inst.dataset_version}」？`)) return;
    setError(null);
    try {
      await api.delete(`/api/datasets/installations/${inst.id}`);
      if (selected === inst.id) setSelected(null);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">数据集</h1>

      <Card>
        <CardHeader>
          <CardTitle>安装本地数据集</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <Label>数据集目录（需包含符合 Dataset Protocol 的 manifest.json）</Label>
            <div className="flex gap-2">
              <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path/to/dataset" className="font-mono" />
              <Button onClick={install} disabled={!path}>
                校验并安装
              </Button>
            </div>
          </div>
          {error && <p className="whitespace-pre-wrap text-sm text-destructive">{error}</p>}
          {info && <p className="text-sm text-emerald-400">{info}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>已安装</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>数据集</TableHead>
                <TableHead>版本</TableHead>
                <TableHead>修订</TableHead>
                <TableHead>协议</TableHead>
                <TableHead>Suites</TableHead>
                <TableHead>安装时间</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {installations.map((inst) => (
                <TableRow key={inst.id}>
                  <TableCell>
                    <div className="font-medium">{inst.dataset_name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{inst.dataset_id}</div>
                  </TableCell>
                  <TableCell>{inst.dataset_version}</TableCell>
                  <TableCell className="font-mono text-xs">{inst.dataset_revision}</TableCell>
                  <TableCell>v{inst.protocol_version}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {inst.suites.map((s) => (
                        <Badge key={s.id} variant="secondary">
                          {s.name}（{s.task_count}）
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmtTime(inst.installed_at)}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button size="sm" variant="outline" onClick={() => showTasks(inst.id)}>
                        查看题目
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => uninstall(inst)}>
                        卸载
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {selected && (
        <Card>
          <CardHeader>
            <CardTitle>题目列表</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>rev</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead>领域</TableHead>
                  <TableHead>污染风险</TableHead>
                  <TableHead>题面</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tasks.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-mono text-xs">{t.task_id}</TableCell>
                    <TableCell>{t.revision}</TableCell>
                    <TableCell>{t.type}</TableCell>
                    <TableCell>{t.domains.join(", ")}</TableCell>
                    <TableCell>
                      <Badge variant={t.contamination === "high" ? "destructive" : "outline"}>{t.contamination}</Badge>
                    </TableCell>
                    <TableCell className="max-w-md truncate">{t.problem}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
