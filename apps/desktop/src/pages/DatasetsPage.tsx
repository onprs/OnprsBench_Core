import { useCallback, useEffect, useRef, useState } from "react";
import { FileArchive, FolderOpen } from "lucide-react";
import { api, type DatasetInstallation, type DatasetTask } from "@/lib/api";
import { cn, fmtTime } from "@/lib/utils";
import { isDesktopApp, listenDirectoryDrop, pickArchiveFile, pickDirectory } from "@/lib/desktop";
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/HelpTip";
import { useConfirm } from "@/components/ConfirmDialog";
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
  const [dragging, setDragging] = useState(false);
  const [sourceMenuOpen, setSourceMenuOpen] = useState(false);
  const sourceMenuRef = useRef<HTMLDivElement>(null);
  const { confirm, confirmElement } = useConfirm();

  const reload = useCallback(async () => {
    setInstallations(await api.get<DatasetInstallation[]>("/api/datasets/installations"));
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function install(target?: string) {
    const directory = (target ?? path).trim();
    if (!directory) return;
    setError(null);
    setInfo(null);
    setSourceMenuOpen(false);
    try {
      const result = await api.post<{ created: boolean; installation: DatasetInstallation }>(
        "/api/datasets/installations",
        { path: directory }
      );
      if (result.installation.distribution === "full") {
        setInfo("安装成功（完整数据集：判定资源已就绪，可离线判定）");
      } else {
        setInfo(result.created ? "安装成功（标准数据集：判定资源已下载）" : "相同内容的数据集已安装，已复用");
      }
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // 桌面应用：监听原生拖入（拖入目录即开始安装）
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let disposed = false;
    void listenDirectoryDrop({
      onDrop: (paths) => {
        if (paths[0]) void install(paths[0]);
      },
      onDragStateChange: setDragging,
    }).then((fn) => {
      if (disposed) fn();
      else unlisten = fn;
    });
    return () => {
      disposed = true;
      unlisten?.();
    };
    // install 依赖 path，但其参数优先，拖入始终使用实际路径
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, reload]);

  /** 点击拖入区域后选择来源：目录或压缩包。 */
  async function pickSource(kind: "directory" | "archive") {
    setSourceMenuOpen(false);
    const selected = kind === "directory" ? await pickDirectory() : await pickArchiveFile();
    if (selected) {
      setPath(selected);
      await install(selected);
    }
  }

  // 点击页面其他位置关闭来源菜单
  useEffect(() => {
    if (!sourceMenuOpen) return;
    function handlePointerDown(event: MouseEvent) {
      if (sourceMenuRef.current && !sourceMenuRef.current.contains(event.target as Node)) {
        setSourceMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [sourceMenuOpen]);

  async function showTasks(installationId: string) {
    setSelected(installationId);
    setTasks(await api.get<DatasetTask[]>(`/api/datasets/installations/${installationId}/tasks`));
  }

  async function uninstall(inst: DatasetInstallation) {
    const ok = await confirm({
      title: "卸载数据集？",
      description: `「${inst.dataset_name} v${inst.dataset_version}」将从本地移除。`,
      confirmText: "卸载",
    });
    if (!ok) return;
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
            <Label>
              数据集目录或发布包
              <HelpTip text="支持解压后的目录，或 .tar.gz / .tgz / .tar 发布包；需包含符合 Dataset Protocol 的 manifest.yaml，安装前会做完整校验" />
            </Label>
            <div className="relative" ref={sourceMenuRef}>
              <button
                type="button"
                onClick={() => {
                  if (!isDesktopApp()) {
                    setError("桌面应用支持点击这里选择数据集；浏览器模式请填写路径");
                    return;
                  }
                  setSourceMenuOpen((open) => !open);
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  if (isDesktopApp()) return; // 桌面壳通过原生拖入事件处理
                  setError("浏览器模式无法读取拖入目录的路径，请在桌面应用中使用拖入或点击选择");
                }}
                className={cn(
                  "flex w-full flex-col items-center justify-center gap-2 rounded-md border border-dashed px-4 py-6 text-center transition-colors",
                  dragging && "border-primary bg-accent/40",
                  isDesktopApp() && "cursor-pointer hover:border-primary/60 hover:bg-accent/20"
                )}
              >
                <FolderOpen className="h-5 w-5 text-muted-foreground" />
                <span className="text-sm text-muted-foreground">
                  {isDesktopApp()
                    ? "将数据集目录或压缩包拖到这里，或点击这里选择（选好即自动安装）"
                    : "桌面应用支持将数据集目录或压缩包拖到这里或点击选择；浏览器模式请填写路径"}
                </span>
                {path && <span className="break-all font-mono text-xs">{path}</span>}
              </button>
              {sourceMenuOpen && (
                <div className="absolute left-1/2 top-full z-50 mt-1 w-44 -translate-x-1/2 rounded-md border bg-popover p-1 shadow-md">
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent"
                    onClick={() => void pickSource("directory")}
                  >
                    <FolderOpen className="h-4 w-4" />
                    选择目录
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent"
                    onClick={() => void pickSource("archive")}
                  >
                    <FileArchive className="h-4 w-4" />
                    选择压缩包
                  </button>
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="数据集目录或 .tar.gz 压缩包路径" className="font-mono" />
              <Button onClick={() => void install()} disabled={!path}>
                校验并安装
              </Button>
            </div>
          </div>
          {error && <p className="whitespace-pre-wrap text-sm text-destructive">{error}</p>}
          {info && <p className="text-sm text-emerald-600 dark:text-emerald-400">{info}</p>}
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
                <TableHead>形态</TableHead>
                <TableHead>修订</TableHead>
                <TableHead>协议</TableHead>
                <TableHead>套件</TableHead>
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
                  <TableCell>
                    {inst.distribution === "full" ? (
                      <Badge variant="success">完整（离线）</Badge>
                    ) : (
                      <Badge variant="secondary">标准</Badge>
                    )}
                  </TableCell>
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

      {confirmElement}
    </div>
  );
}
