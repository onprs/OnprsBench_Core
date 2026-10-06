import { useCallback, useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";
import { api, type Deployment, type ModelInfo, type Provider, type ProviderType, type ReasoningProfile } from "@/lib/api";
import { cn, fmtTime } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/HelpTip";
import { useConfirm } from "@/components/ConfirmDialog";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectEmpty, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function ErrorText({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="text-sm text-destructive">{error}</p>;
}

/** 从渠道模型列表选用模型时，填入部署创建表单的草稿。 */
export interface DeploymentDraft {
  providerId: string;
  modelId: string;
  apiModelName: string;
}

export function SetupPage() {
  const [providerTypes, setProviderTypes] = useState<ProviderType[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [profiles, setProfiles] = useState<ReasoningProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(() =>
    typeof window !== "undefined" && window.location.hash.includes("tab=deployments")
      ? "deployments"
      : "providers"
  );
  const [deploymentDraft, setDeploymentDraft] = useState<DeploymentDraft | null>(null);

  const reload = useCallback(async () => {
    try {
      const [types, provs, mods, deps, profs] = await Promise.all([
        api.get<{ types: ProviderType[] }>("/api/provider-types"),
        api.get<Provider[]>("/api/providers"),
        api.get<ModelInfo[]>("/api/models"),
        api.get<Deployment[]>("/api/deployments"),
        api.get<ReasoningProfile[]>("/api/reasoning-profiles"),
      ]);
      setProviderTypes(types.types);
      setProviders(provs);
      setModels(mods);
      setDeployments(deps);
      setProfiles(profs);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">设置</h1>
      <ErrorText error={error} />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="providers">渠道</TabsTrigger>
          <TabsTrigger value="deployments">部署</TabsTrigger>
        </TabsList>
        <TabsContent value="providers" forceMount className="data-[state=inactive]:hidden">
          <ProvidersTab
            providerTypes={providerTypes}
            providers={providers}
            deployments={deployments}
            onChanged={reload}
            onUseModel={(draft) => {
              setDeploymentDraft(draft);
              setTab("deployments");
            }}
          />
        </TabsContent>
        <TabsContent value="deployments" forceMount className="data-[state=inactive]:hidden">
          <DeploymentsTab
            providers={providers}
            models={models}
            deployments={deployments}
            profiles={profiles}
            onChanged={reload}
            draft={deploymentDraft}
            onDraftApplied={() => setDeploymentDraft(null)}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Provider 标签页
// ---------------------------------------------------------------------------

function ProvidersTab({
  providerTypes,
  providers,
  deployments,
  onChanged,
  onUseModel,
}: {
  providerTypes: ProviderType[];
  providers: Provider[];
  deployments: Deployment[];
  onChanged: () => Promise<void>;
  onUseModel: (draft: DeploymentDraft) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState("mock");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  // 模型面板：点击 Provider 展示已拉取列表；拉取按钮负责拉取/再次拉取
  const [modelsPanel, setModelsPanel] = useState<{
    provider: Provider;
    models: string[] | null;
    error: string | null;
    fetchedAt: string | null;
    source: "loading" | "cached" | "fresh" | "empty";
  } | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);
  const { confirm, confirmElement } = useConfirm();

  const selectedType = providerTypes.find((t) => t.type === type);

  async function createProvider() {
    setError(null);
    try {
      await api.post("/api/providers", {
        name,
        type,
        base_url: baseUrl || null,
        api_key: apiKey || null,
      });
      setName("");
      setBaseUrl("");
      setApiKey("");
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  /** 点击 Provider 条目：展示已拉取的列表（不发起拉取）。 */
  async function showCachedModels(provider: Provider) {
    setPanelOpen(true);
    if (provider.model_catalog_count === 0) {
      setModelsPanel({ provider, models: [], error: null, fetchedAt: null, source: "empty" });
      return;
    }
    setModelsPanel({
      provider,
      models: null,
      error: null,
      fetchedAt: provider.model_catalog_fetched_at,
      source: "loading",
    });
    try {
      const data = await api.get<{ models: string[]; fetched_at: string | null }>(
        `/api/providers/${provider.id}/models`
      );
      setModelsPanel({
        provider,
        models: data.models,
        error: null,
        fetchedAt: data.fetched_at,
        source: "cached",
      });
    } catch (e) {
      setModelsPanel({
        provider,
        models: null,
        error: e instanceof Error ? e.message : String(e),
        fetchedAt: provider.model_catalog_fetched_at,
        source: "cached",
      });
    }
  }

  /** 拉取按钮：拉取 / 再次拉取（强制刷新并更新缓存）。 */
  async function fetchModels(provider: Provider) {
    setPanelOpen(true);
    setModelsPanel({
      provider,
      models: null,
      error: null,
      fetchedAt: provider.model_catalog_fetched_at,
      source: "loading",
    });
    try {
      const data = await api.get<{ models: string[]; fetched_at: string | null }>(
        `/api/providers/${provider.id}/models?refresh=true`
      );
      setModelsPanel({
        provider,
        models: data.models,
        error: null,
        fetchedAt: data.fetched_at,
        source: "fresh",
      });
      await onChanged();
    } catch (e) {
      setModelsPanel({
        provider,
        models: null,
        error: e instanceof Error ? e.message : String(e),
        fetchedAt: provider.model_catalog_fetched_at,
        source: "fresh",
      });
    }
  }

  async function deleteProvider(provider: Provider) {
    if (!(await confirm({
      title: "删除渠道？",
      description: `「${provider.name}」的 API Key 会一并从系统安全存储移除。`,
      confirmText: "删除",
    }))) return;
    setListError(null);
    try {
      await api.delete(`/api/providers/${provider.id}`);
      await onChanged();
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    }
  }

  async function useRemoteModel(provider: Provider, apiModelName: string) {
    // 仅确保 canonical Model 存在，并填入部署创建表单；由用户确认后创建部署
    setListError(null);
    try {
      const model = await api.post<ModelInfo>("/api/models", {
        canonical_id: apiModelName,
        display_name: apiModelName,
      });
      await onChanged();
      onUseModel({ providerId: provider.id, modelId: model.id, apiModelName });
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="flex items-start gap-4">
      <div className="grid min-w-0 flex-1 items-start gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>添加渠道</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：OpenAI 官方" />
          </div>
          <div className="space-y-1">
            <Label>类型</Label>
            <Select value={type} onValueChange={setType}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providerTypes.map((t) => (
                  <SelectItem key={t.type} value={t.type}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>
              接口地址
              {selectedType?.base_url && <HelpTip text={`默认 ${selectedType.base_url}`} />}
            </Label>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="可选" />
          </div>
          {type !== "mock" && (
            <div className="space-y-1">
              <Label>
                API Key
                <HelpTip text="存入系统安全存储，不写入数据库；也可创建后再补" />
              </Label>
              <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
            </div>
          )}
          <ErrorText error={error} />
          <Button onClick={createProvider} disabled={!name}>
            创建
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>已有渠道</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorText error={listError} />
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead className="w-[104px]">类型</TableHead>
                <TableHead className="w-[76px]">凭据</TableHead>
                <TableHead className="w-[216px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {providers.map((p) => (
                <TableRow
                  key={p.id}
                  className="cursor-pointer hover:bg-muted/50"
                  title="查看已拉取的模型列表"
                  onClick={() => void showCachedModels(p)}
                >
                  <TableCell className="break-words font-medium">{p.name}</TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">{providerTypes.find((t) => t.type === p.type)?.label ?? p.type}</TableCell>
                  <TableCell className="whitespace-nowrap">{p.has_credential ? <Badge variant="success">已保存</Badge> : <Badge variant="outline">未设置</Badge>}</TableCell>
                  {/* 操作列阻止冒泡，避免点按钮时同时打开模型面板 */}
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <div className="flex justify-end gap-1">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void fetchModels(p)}
                        disabled={panelOpen && modelsPanel?.provider.id === p.id && modelsPanel.source === "loading"}
                      >
                        {panelOpen && modelsPanel?.provider.id === p.id && modelsPanel.source === "loading" ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : null}
                        {p.model_catalog_count > 0 ? "再次拉取" : "拉取模型"}
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => setEditing(p)}>
                        编辑
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => deleteProvider(p)}>
                        删除
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <ProviderEditDialog provider={editing} onClose={() => setEditing(null)} onSaved={onChanged} />
      </div>

      {/* 页内右侧面板：随宽度过渡动画滑出，无遮罩叠层 */}
      <div
        className={cn(
          "shrink-0 overflow-hidden transition-all duration-300 ease-in-out",
          panelOpen ? "w-[360px] opacity-100" : "w-0 opacity-0"
        )}
      >
        <div className="w-[360px]">
          <Card className="relative">
            <Button
              size="icon"
              variant="ghost"
              className="absolute right-1.5 top-1.5 h-7 w-7"
              title="关闭"
              onClick={() => setPanelOpen(false)}
            >
              <X className="h-4 w-4" />
            </Button>
            <CardHeader className="space-y-0 pr-10">
              <CardTitle className="flex items-center gap-1.5 text-base">
                {modelsPanel?.provider.name} 的可用模型
                <HelpTip text="选择模型后填入部署创建表单，由你确认后创建" />
              </CardTitle>
            </CardHeader>
            <CardContent>
              {modelsPanel && modelsPanel.source !== "loading" && !modelsPanel.error && (
                <p className="mb-2 text-xs text-muted-foreground">
                  {modelsPanel.source === "cached" &&
                    `已拉取列表${modelsPanel.fetchedAt ? ` · ${fmtTime(modelsPanel.fetchedAt)}` : ""}`}
                  {modelsPanel.source === "fresh" &&
                    `刚刚拉取${modelsPanel.fetchedAt ? ` · ${fmtTime(modelsPanel.fetchedAt)}` : ""}`}
                  {modelsPanel.source === "empty" && "尚未拉取模型"}
                </p>
              )}
              {modelsPanel?.source === "loading" && (
                <div className="flex h-40 flex-col items-center justify-center gap-2 text-muted-foreground">
                  <Loader2 className="h-6 w-6 animate-spin" />
                  <span className="text-sm">正在拉取模型列表…</span>
                </div>
              )}
              {modelsPanel?.error && (
                <div className="flex h-40 flex-col items-center justify-center gap-3">
                  <p className="text-sm text-destructive">{modelsPanel.error}</p>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      modelsPanel.source === "fresh"
                        ? void fetchModels(modelsPanel.provider)
                        : void showCachedModels(modelsPanel.provider)
                    }
                  >
                    重试
                  </Button>
                </div>
              )}
              {modelsPanel?.source === "empty" && !modelsPanel.error && (
                <p className="text-sm text-muted-foreground">
                  尚未拉取模型。点击右侧「拉取模型」获取该渠道的可用模型列表。
                </p>
              )}
              {modelsPanel?.models && modelsPanel.models.length === 0 && modelsPanel.source !== "empty" && (
                <p className="text-sm text-muted-foreground">该渠道未返回任何模型</p>
              )}
              {modelsPanel?.models && modelsPanel.models.length > 0 && (
                <div className="max-h-[60vh] space-y-1 overflow-auto">
                  {modelsPanel.models.map((m) => {
                    const added = deployments.some(
                      (d) => d.provider_id === modelsPanel.provider.id && d.api_model_name === m
                    );
                    return (
                      <div key={m} className="flex items-center justify-between rounded border px-3 py-1.5 text-sm">
                        <span className="font-mono">{m}</span>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={added}
                          onClick={() => void useRemoteModel(modelsPanel.provider, m)}
                        >
                          {added ? "已有部署" : "选择"}
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
      {confirmElement}
    </div>
  );
}

function ProviderEditDialog({
  provider,
  onClose,
  onSaved,
}: {
  provider: Provider | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(provider?.name ?? "");
    setBaseUrl(provider?.base_url ?? "");
    setApiKey("");
    setError(null);
  }, [provider]);

  async function save() {
    if (!provider) return;
    setError(null);
    try {
      await api.patch(`/api/providers/${provider.id}`, {
        name: name || undefined,
        base_url: baseUrl || undefined,
        api_key: apiKey || undefined,
      });
      onClose();
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Dialog open={provider !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑渠道</DialogTitle>
          <DialogDescription>凭据状态：{provider?.has_credential ? "已保存 API Key" : "未设置 API Key"}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>接口地址</Label>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="可选" />
          </div>
          {provider?.type !== "mock" && (
            <div className="space-y-1">
              <Label>
                新 API Key
                <HelpTip text="留空则保持不变；输入新值则替换已保存的凭据" />
              </Label>
              <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
            </div>
          )}
          <ErrorText error={error} />
          <Button onClick={save} disabled={!name}>
            保存
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function numberOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Reasoning Profile 关键参数摘要（列表悬浮提示） */
function describeProfile(profile: ReasoningProfile): string {
  const parts: string[] = [];
  if (profile.reasoning_effort) parts.push(`思考程度 ${profile.reasoning_effort}`);
  if (profile.reasoning_budget !== null) parts.push(`思考预算 ${profile.reasoning_budget}`);
  if (profile.max_output_tokens !== null) parts.push(`输出上限 ${profile.max_output_tokens}`);
  parts.push(
    profile.agent_max_turns === null
      ? "轮次默认"
      : profile.agent_max_turns === 0
        ? "轮次不限制"
        : `轮次上限 ${profile.agent_max_turns}`
  );
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// Deployment 标签页
// ---------------------------------------------------------------------------

function DeploymentsTab({
  providers,
  models,
  deployments,
  profiles,
  onChanged,
  draft,
  onDraftApplied,
}: {
  providers: Provider[];
  models: ModelInfo[];
  deployments: Deployment[];
  profiles: ReasoningProfile[];
  onChanged: () => Promise<void>;
  draft: DeploymentDraft | null;
  onDraftApplied: () => void;
}) {
  const [name, setName] = useState("");
  const [modelId, setModelId] = useState("");
  const [providerId, setProviderId] = useState("");
  const [apiModelName, setApiModelName] = useState("");
  const [priceIn, setPriceIn] = useState("");
  const [priceOut, setPriceOut] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [draftHint, setDraftHint] = useState<string | null>(null);

  const [profileFor, setProfileFor] = useState<string | null>(null);
  const [profileName, setProfileName] = useState("");
  const [profileEffort, setProfileEffort] = useState("");
  const [profileBudget, setProfileBudget] = useState("");
  const [profileMaxTokens, setProfileMaxTokens] = useState("");
  const [profileMaxTurns, setProfileMaxTurns] = useState("");
  const [profileTemp, setProfileTemp] = useState("");
  const [editing, setEditing] = useState<Deployment | null>(null);
  const { confirm, confirmElement } = useConfirm();

  // 从渠道模型列表选用时预填表单，由用户确认后创建
  useEffect(() => {
    if (!draft) return;
    setName(`${draft.apiModelName} · `);
    setModelId(draft.modelId);
    setProviderId(draft.providerId);
    setApiModelName(draft.apiModelName);
    setDraftHint(`已填入「${draft.apiModelName}」，确认后点击创建部署`);
    onDraftApplied();
  }, [draft, onDraftApplied]);

  async function createDeployment() {
    setError(null);
    try {
      await api.post("/api/deployments", {
        name,
        model_id: modelId,
        provider_id: providerId,
        api_model_name: apiModelName,
        price_input_per_mtok: priceIn ? Number(priceIn) : null,
        price_output_per_mtok: priceOut ? Number(priceOut) : null,
      });
      setName("");
      setApiModelName("");
      setPriceIn("");
      setPriceOut("");
      setDraftHint(null);
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function deleteDeployment(d: Deployment) {
    const ok = await confirm({
      title: "删除部署？",
      description: `「${d.name}」及其未被历史使用的推理配置将被删除。`,
      confirmText: "删除",
    });
    if (!ok) return;
    setListError(null);
    try {
      await api.delete(`/api/deployments/${d.id}`);
      await onChanged();
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    }
  }

  async function deleteProfile(p: ReasoningProfile) {
    const ok = await confirm({
      title: "删除推理配置？",
      description: `「${p.name}」将被删除。`,
      confirmText: "删除",
    });
    if (!ok) return;
    setListError(null);
    try {
      await api.delete(`/api/reasoning-profiles/${p.id}`);
      await onChanged();
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    }
  }

  async function createProfile() {
    if (!profileFor) return;
    setError(null);
    try {
      await api.post("/api/reasoning-profiles", {
        deployment_id: profileFor,
        name: profileName,
        reasoning_effort: profileEffort.trim() || null,
        reasoning_budget: numberOrNull(profileBudget),
        max_output_tokens: numberOrNull(profileMaxTokens),
        agent_max_turns: profileMaxTurns.trim() === "" ? null : numberOrNull(profileMaxTurns),
        temperature: numberOrNull(profileTemp),
      });
      setProfileFor(null);
      setProfileName("");
      setProfileEffort("");
      setProfileBudget("");
      setProfileMaxTokens("");
      setProfileMaxTurns("");
      setProfileTemp("");
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>手动创建部署</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 lg:grid-cols-3">
          {draftHint && <p className="text-sm text-primary lg:col-span-3">{draftHint}</p>}
          <div className="space-y-1">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：Kimi K3 · Official" />
          </div>
          <div className="space-y-1">
            <Label>模型</Label>
            <Select value={modelId} onValueChange={setModelId}>
              <SelectTrigger>
                <SelectValue placeholder="选择模型" />
              </SelectTrigger>
              <SelectContent>
                {models.length === 0 && <SelectEmpty>暂无模型（可在渠道页拉取模型后创建）</SelectEmpty>}
                {models.map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>渠道</Label>
            <Select value={providerId} onValueChange={setProviderId}>
              <SelectTrigger>
                <SelectValue placeholder="选择渠道" />
              </SelectTrigger>
              <SelectContent>
                {providers.length === 0 && <SelectEmpty>暂无渠道（请先在渠道页添加）</SelectEmpty>}
                {providers.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}（{p.type}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>API 模型名</Label>
            <Input value={apiModelName} onChange={(e) => setApiModelName(e.target.value)} placeholder="例如：gpt-4o" />
          </div>
          <div className="space-y-1">
            <Label>
              输入价（$/M tokens）
              <HelpTip text="手动价格设置，优先级高于 models.dev 与 LiteLLM 价格目录" />
            </Label>
            <Input value={priceIn} onChange={(e) => setPriceIn(e.target.value)} placeholder="可选" />
          </div>
          <div className="space-y-1">
            <Label>
              输出价（$/M tokens）
              <HelpTip text="手动价格设置，优先级高于 models.dev 与 LiteLLM 价格目录" />
            </Label>
            <Input value={priceOut} onChange={(e) => setPriceOut(e.target.value)} placeholder="可选" />
          </div>
          <div className="lg:col-span-3">
            <ErrorText error={error} />
            <Button onClick={createDeployment} disabled={!name || !modelId || !providerId || !apiModelName}>
              创建部署
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>部署列表</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorText error={listError} />
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[21%]">名称</TableHead>
                <TableHead className="w-[12.5%]">模型</TableHead>
                <TableHead className="w-[12.5%]">渠道</TableHead>
                <TableHead className="w-[12%]">API 模型名</TableHead>
                <TableHead className="w-[8%]">推理配置</TableHead>
                <TableHead className="w-[140px]">创建时间</TableHead>
                <TableHead className="w-[248px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {deployments.map((d) => (
                <TableRow key={d.id}>
                  <TableCell className="break-words font-medium">{d.name}</TableCell>
                  <TableCell className="break-words">{d.model_display_name}</TableCell>
                  <TableCell className="break-words">{d.provider_name}</TableCell>
                  <TableCell className="break-words font-mono text-xs">{d.api_model_name}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {profiles
                        .filter((p) => p.deployment_id === d.id)
                        .map((p) => (
                          <Badge key={p.id} variant="secondary" className="gap-1" title={describeProfile(p)}>
                            {p.name}
                            <button
                              type="button"
                              className="opacity-60 hover:opacity-100"
                              onClick={() => deleteProfile(p)}
                              title="删除该推理配置"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </Badge>
                        ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmtTime(d.created_at)}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="outline" onClick={() => setProfileFor(d.id)}>
                        + 推理配置
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => setEditing(d)}>
                        编辑
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => deleteDeployment(d)}>
                        删除
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={profileFor !== null} onOpenChange={(open) => !open && setProfileFor(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-1.5">
              新建推理配置
              <HelpTip text="同一部署的不同思考强度是独立的评测配置" />
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>名称</Label>
              <Input value={profileName} onChange={(e) => setProfileName(e.target.value)} placeholder="low / medium / high" />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label>思考程度</Label>
                <Input value={profileEffort} onChange={(e) => setProfileEffort(e.target.value)} placeholder="low / medium / high / max" />
              </div>
              <div className="space-y-1">
                <Label className="flex items-center gap-1">
                  思考预算（token）
                  <HelpTip text="仅部分渠道支持；留空表示不指定" />
                </Label>
                <Input value={profileBudget} onChange={(e) => setProfileBudget(e.target.value)} placeholder="留空 = 不指定" />
              </div>
              <div className="space-y-1">
                <Label className="flex items-center gap-1">
                  最大输出 token
                  <HelpTip text="思考型模型的思考与回答共享该预算；留空表示不限制" />
                </Label>
                <Input value={profileMaxTokens} onChange={(e) => setProfileMaxTokens(e.target.value)} placeholder="留空 = 不限制" />
              </div>
              <div className="space-y-1">
                <Label className="flex items-center gap-1">
                  最大执行轮次
                  <HelpTip text="Agent 在任务中最多执行多少轮工具调用；留空使用默认上限，填 0 表示不限制" />
                </Label>
                <Input value={profileMaxTurns} onChange={(e) => setProfileMaxTurns(e.target.value)} placeholder="留空 = 默认，0 = 不限制" />
              </div>
              <div className="space-y-1">
                <Label>采样温度</Label>
                <Input value={profileTemp} onChange={(e) => setProfileTemp(e.target.value)} placeholder="留空 = 不指定" />
              </div>
            </div>
            <Button onClick={createProfile} disabled={!profileName}>
              创建
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <DeploymentEditDialog deployment={editing} onClose={() => setEditing(null)} onSaved={onChanged} />
      {confirmElement}
    </div>
  );
}

function DeploymentEditDialog({
  deployment,
  onClose,
  onSaved,
}: {
  deployment: Deployment | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [apiModelName, setApiModelName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [priceIn, setPriceIn] = useState("");
  const [priceOut, setPriceOut] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(deployment?.name ?? "");
    setApiModelName(deployment?.api_model_name ?? "");
    setEndpoint(deployment?.endpoint_override ?? "");
    setPriceIn(deployment?.price_input_per_mtok?.toString() ?? "");
    setPriceOut(deployment?.price_output_per_mtok?.toString() ?? "");
    setError(null);
  }, [deployment]);

  async function save() {
    if (!deployment) return;
    setError(null);
    try {
      await api.patch(`/api/deployments/${deployment.id}`, {
        name: name || undefined,
        api_model_name: apiModelName || undefined,
        endpoint_override: endpoint || null,
        price_input_per_mtok: priceIn ? Number(priceIn) : null,
        price_output_per_mtok: priceOut ? Number(priceOut) : null,
      });
      onClose();
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Dialog open={deployment !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑部署</DialogTitle>
          <DialogDescription>{deployment?.model_display_name} · {deployment?.provider_name}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>API 模型名</Label>
            <Input value={apiModelName} onChange={(e) => setApiModelName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>接口地址覆盖</Label>
            <Input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="可选" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>
                输入价（$/M tokens）
                <HelpTip text="手动价格设置，优先级高于 models.dev 与 LiteLLM 价格目录" />
              </Label>
              <Input value={priceIn} onChange={(e) => setPriceIn(e.target.value)} placeholder="可选" />
            </div>
            <div className="space-y-1">
              <Label>
                输出价（$/M tokens）
                <HelpTip text="手动价格设置，优先级高于 models.dev 与 LiteLLM 价格目录" />
              </Label>
              <Input value={priceOut} onChange={(e) => setPriceOut(e.target.value)} placeholder="可选" />
            </div>
          </div>
          <ErrorText error={error} />
          <Button onClick={save} disabled={!name || !apiModelName}>
            保存
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
