import { useCallback, useEffect, useState } from "react";
import { Loader2, Check, ChevronDown, X } from "lucide-react";
import { api, type Deployment, type ModelInfo, type PricingPreview, type PricingPreviewCapabilities, type Provider, type ProviderType, type ReasoningProfile } from "@/lib/api";
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
import { CompactList } from "@/components/CompactList";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { labelForPriceSource } from "@/lib/labels";

function ErrorText({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="text-sm text-destructive">{error}</p>;
}

export function SetupPage() {
  const [providerTypes, setProviderTypes] = useState<ProviderType[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [profiles, setProfiles] = useState<ReasoningProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(() =>
    typeof window !== "undefined" && window.location.hash.includes("tab=deployments")
      ? "deployments"
      : "providers"
  );
  const reload = useCallback(async () => {
    try {
      const [types, provs, deps, profs] = await Promise.all([
        api.get<{ types: ProviderType[] }>("/api/provider-types"),
        api.get<Provider[]>("/api/providers"),
        api.get<Deployment[]>("/api/deployments"),
        api.get<ReasoningProfile[]>("/api/reasoning-profiles"),
      ]);
      setProviderTypes(types.types);
      setProviders(provs);
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
          />
        </TabsContent>
        <TabsContent value="deployments" forceMount className="data-[state=inactive]:hidden">
          <DeploymentsTab
            providers={providers}
            providerTypes={providerTypes}
            deployments={deployments}
            profiles={profiles}
            onChanged={reload}
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
}: {
  providerTypes: ProviderType[];
  providers: Provider[];
  deployments: Deployment[];
  onChanged: () => Promise<void>;
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
    selected: string[];
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
      setModelsPanel({ provider, models: [], selected: [], error: null, fetchedAt: null, source: "empty" });
      return;
    }
    setModelsPanel({
      provider,
      models: null,
      selected: [],
      error: null,
      fetchedAt: provider.model_catalog_fetched_at,
      source: "loading",
    });
    try {
      const data = await api.get<{ models: string[]; selected_models: string[]; fetched_at: string | null }>(
        `/api/providers/${provider.id}/models`
      );
      setModelsPanel({
        provider,
        models: data.models,
        selected: data.selected_models ?? [],
        error: null,
        fetchedAt: data.fetched_at,
        source: "cached",
      });
    } catch (e) {
      setModelsPanel({
        provider,
        models: null,
        selected: [],
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
      selected: [],
      error: null,
      fetchedAt: provider.model_catalog_fetched_at,
      source: "loading",
    });
    try {
      const data = await api.get<{ models: string[]; selected_models: string[]; fetched_at: string | null }>(
        `/api/providers/${provider.id}/models?refresh=true`
      );
      setModelsPanel({
        provider,
        models: data.models,
        selected: data.selected_models ?? [],
        error: null,
        fetchedAt: data.fetched_at,
        source: "fresh",
      });
      await onChanged();
    } catch (e) {
      setModelsPanel({
        provider,
        models: null,
        selected: [],
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

  /** 勾选/取消勾选模型：已选模型在部署页可选。 */
  async function toggleModel(providerId: string, model: string) {
    try {
      const data = await api.post<{ selected_models: string[] }>(
        `/api/providers/${providerId}/models/toggle`,
        { model }
      );
      setModelsPanel((panel) => (panel ? { ...panel, selected: data.selected_models ?? [] } : panel));
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
                  {modelsPanel.selected.length > 0 ? ` · 已选 ${modelsPanel.selected.length} 个` : ""}
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
                  {[
                    ...modelsPanel.selected.filter((m) => modelsPanel.models?.includes(m)),
                    ...modelsPanel.models.filter((m) => !modelsPanel.selected.includes(m)),
                  ].map((m) => {
                    const isSelected = modelsPanel.selected.includes(m);
                    const hasDeployment = deployments.some(
                      (d) => d.provider_id === modelsPanel.provider.id && d.api_model_name === m
                    );
                    return (
                      <div
                        key={m}
                        className={cn(
                          "flex items-center justify-between rounded border px-3 py-1.5 text-sm",
                          isSelected && "border-primary/40 bg-accent/40"
                        )}
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <span className="truncate font-mono">{m}</span>
                          {hasDeployment && <Badge variant="outline">已有部署</Badge>}
                        </span>
                        <Button
                          size="sm"
                          variant={isSelected ? "outline" : "default"}
                          onClick={() => void toggleModel(modelsPanel.provider.id, m)}
                        >
                          {isSelected ? (
                            <>
                              <Check className="mr-1 h-3.5 w-3.5" />
                              取消选择
                            </>
                          ) : (
                            "选择"
                          )}
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

/** 推理配置表单草稿（输入框值以字符串保存，提交时转换）。 */
interface ProfileDraft {
  name: string;
  effort: string;
  budget: string;
  maxTokens: string;
  maxTurns: string;
  temperature: string;
}

const EMPTY_PROFILE_DRAFT: ProfileDraft = {
  name: "",
  effort: "",
  budget: "",
  maxTokens: "",
  maxTurns: "",
  temperature: "",
};

function draftToProfilePayload(draft: ProfileDraft) {
  return {
    name: draft.name.trim() || "默认",
    reasoning_effort: draft.effort.trim() || null,
    reasoning_budget: numberOrNull(draft.budget),
    max_output_tokens: numberOrNull(draft.maxTokens),
    agent_max_turns: draft.maxTurns.trim() === "" ? null : numberOrNull(draft.maxTurns),
    temperature: numberOrNull(draft.temperature),
  };
}

function draftFromProfile(profile: ReasoningProfile): ProfileDraft {
  return {
    name: profile.name,
    effort: profile.reasoning_effort ?? "",
    budget: profile.reasoning_budget !== null ? String(profile.reasoning_budget) : "",
    maxTokens: profile.max_output_tokens !== null ? String(profile.max_output_tokens) : "",
    maxTurns: profile.agent_max_turns !== null ? String(profile.agent_max_turns) : "",
    temperature: profile.temperature !== null ? String(profile.temperature) : "",
  };
}

/** 推理配置字段（创建部署与编辑对话框共用）。 */
function ProfileFields({
  draft,
  onChange,
  capabilities,
}: {
  draft: ProfileDraft;
  onChange: (patch: Partial<ProfileDraft>) => void;
  capabilities: PricingPreviewCapabilities | null;
}) {
  const reasoningSupported = capabilities?.reasoning !== false;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1 sm:col-span-2">
        <Label className="flex h-5 items-center gap-1">配置名称</Label>
        <Input
          value={draft.name}
          onChange={(e) => onChange({ name: e.target.value })}
          placeholder="默认"
        />
      </div>
      <div className="space-y-1">
        <Label className="flex h-5 items-center gap-1">
          思考程度
          {!reasoningSupported && <HelpTip text="该模型未声明推理能力，此处设置可能被忽略" />}
        </Label>
        <Input
          value={draft.effort}
          onChange={(e) => onChange({ effort: e.target.value })}
          placeholder="low / medium / high / max"
          disabled={!reasoningSupported}
        />
      </div>
      <div className="space-y-1">
        <Label className="flex h-5 items-center gap-1">思考预算（token）</Label>
        <Input
          value={draft.budget}
          onChange={(e) => onChange({ budget: e.target.value })}
          placeholder="留空 = 不指定"
          disabled={!reasoningSupported}
        />
      </div>
      <div className="space-y-1">
        <Label className="flex h-5 items-center gap-1">
          最大输出 token
          {capabilities?.output_limit ? <HelpTip text={`该模型声明上限 ${capabilities.output_limit}`} /> : null}
        </Label>
        <Input
          value={draft.maxTokens}
          onChange={(e) => onChange({ maxTokens: e.target.value })}
          placeholder="留空 = 不限制"
        />
      </div>
      <div className="space-y-1">
        <Label className="flex h-5 items-center gap-1">最大执行轮次</Label>
        <Input
          value={draft.maxTurns}
          onChange={(e) => onChange({ maxTurns: e.target.value })}
          placeholder="留空 = 默认，0 = 不限制"
        />
      </div>
      <div className="space-y-1">
        <Label className="flex h-5 items-center gap-1">采样温度</Label>
        <Input
          value={draft.temperature}
          onChange={(e) => onChange({ temperature: e.target.value })}
          placeholder="留空 = 不指定"
        />
      </div>
    </div>
  );
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
  providerTypes,
  deployments,
  profiles,
  onChanged,
}: {
  providers: Provider[];
  providerTypes: ProviderType[];
  deployments: Deployment[];
  profiles: ReasoningProfile[];
  onChanged: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [modelId, setModelId] = useState("");
  const [providerId, setProviderId] = useState("");
  const [apiModelName, setApiModelName] = useState("");
  const [priceIn, setPriceIn] = useState("");
  const [priceOut, setPriceOut] = useState("");
  const [priceCachedIn, setPriceCachedIn] = useState("");  const [priceCachedWrite, setPriceCachedWrite] = useState("");
  const [endpointOverride, setEndpointOverride] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const [providerModels, setProviderModels] = useState<string[]>([]);
  const [profileDraft, setProfileDraft] = useState<ProfileDraft>(EMPTY_PROFILE_DRAFT);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [priceSource, setPriceSource] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<PricingPreviewCapabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const [editing, setEditing] = useState<Deployment | null>(null);
  const { confirm, confirmElement } = useConfirm();

  /** 加载渠道已勾选的模型（部署只从已选模型中选择）。 */
  async function loadProviderModels(id: string): Promise<string[]> {
    setModelsLoading(true);
    try {
      const data = await api.get<{ models: string[]; selected_models: string[] }>(
        `/api/providers/${id}/models`
      );
      const selected = data.selected_models ?? [];
      setProviderModels(selected);
      return selected;
    } catch (e) {
      setError(`获取渠道模型失败：${e instanceof Error ? e.message : String(e)}`);
      setProviderModels([]);
      return [];
    } finally {
      setModelsLoading(false);
    }
  }

  /** 选择模型后的自动匹配：canonical Model、默认名称、价格与模型能力。 */
  async function applyModelDefaults(provider: string, apiModel: string): Promise<void> {
    const model = await api.post<ModelInfo>("/api/models", {
      canonical_id: apiModel,
      display_name: apiModel,
    });
    setModelId(model.id);
    setApiModelName(apiModel);
    const providerName = providers.find((p) => p.id === provider)?.name;
    setName((current) =>
      current.trim() ? current : providerName ? `${apiModel} · ${providerName}` : apiModel
    );
    const preview = await api.get<PricingPreview>(
      `/api/pricing/preview?provider_id=${encodeURIComponent(provider)}&api_model_name=${encodeURIComponent(apiModel)}`
    );
    setPriceSource(preview.source);
    setCapabilities(preview.capabilities);
    setPriceIn(preview.price_input_per_mtok !== null ? String(preview.price_input_per_mtok) : "");
    setPriceOut(preview.price_output_per_mtok !== null ? String(preview.price_output_per_mtok) : "");
    setPriceCachedIn(
      preview.price_cached_input_per_mtok !== null ? String(preview.price_cached_input_per_mtok) : ""
    );
    setPriceCachedWrite(
      preview.price_cache_write_per_mtok !== null ? String(preview.price_cache_write_per_mtok) : ""
    );
  }

  // 未匹配到价格时自动展开「更多设置」，便于手动填写
  useEffect(() => {
    if (priceSource === "unknown") setMoreOpen(true);
  }, [priceSource]);

  async function handleProviderChange(id: string) {
    setProviderId(id);
    setProviderModels([]);
    setApiModelName("");
    setModelId("");
    setPriceSource(null);
    setCapabilities(null);
    if (!id) return;
    const list = await loadProviderModels(id);
    if (list.length === 0) setError("该渠道还没有选中的模型，请先在渠道页选择模型");
  }

  async function handleModelChange(apiModel: string) {
    if (!apiModel) return;
    setError(null);
    try {
      await applyModelDefaults(providerId, apiModel);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function createDeployment() {
    setError(null);
    try {
      const deployment = await api.post<{ id: string }>("/api/deployments", {
        name,
        model_id: modelId,
        provider_id: providerId,
        api_model_name: apiModelName,
        endpoint_override: endpointOverride.trim() || null,
        price_input_per_mtok: numberOrNull(priceIn),
        price_output_per_mtok: numberOrNull(priceOut),
        price_cached_input_per_mtok: numberOrNull(priceCachedIn),
        price_cache_write_per_mtok: numberOrNull(priceCachedWrite),
      });
      // 填写了推理配置时随部署一并创建
      const profileTouched =
        profileDraft.name.trim() !== "" ||
        Object.entries(profileDraft).some(([key, value]) => key !== "name" && value.trim() !== "");
      if (profileTouched) {
        await api.post("/api/reasoning-profiles", {
          deployment_id: deployment.id,
          ...draftToProfilePayload(profileDraft),
        });
      }
      setName("");
      setApiModelName("");
      setModelId("");
      setPriceIn("");
      setPriceOut("");
      setPriceCachedIn("");
      setPriceCachedWrite("");
      setEndpointOverride("");
      setPriceSource(null);
      setCapabilities(null);
      setProfileDraft(EMPTY_PROFILE_DRAFT);
      setMoreOpen(false);
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

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>手动创建部署</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 lg:grid-cols-3">
            <div className="space-y-1">
              <Label className="flex h-5 items-center gap-1">名称</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：Kimi K3 · Official" />
            </div>
            <div className="space-y-1">
              <Label className="flex h-5 items-center gap-1">渠道</Label>
              <Select value={providerId} onValueChange={(value) => void handleProviderChange(value)}>
                <SelectTrigger>
                  <SelectValue placeholder="选择渠道" />
                </SelectTrigger>
                <SelectContent>
                  {providers.length === 0 && <SelectEmpty>暂无渠道（请先在渠道页添加）</SelectEmpty>}
                  {providers.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}（{providerTypes.find((t) => t.type === p.type)?.label ?? p.type}）
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="flex h-5 items-center gap-1">
                模型
                <HelpTip text="来自该渠道已勾选的模型；选择后自动匹配价格与配置，无需填写 API 模型名" />
              </Label>
              <Select
                value={apiModelName}
                onValueChange={(value) => void handleModelChange(value)}
                disabled={!providerId || modelsLoading}
              >
                <SelectTrigger>
                  <SelectValue placeholder={modelsLoading ? "加载中…" : "选择模型"} />
                </SelectTrigger>
                <SelectContent>
                  {!providerId && <SelectEmpty>请先选择渠道</SelectEmpty>}
                  {providerId && !modelsLoading && providerModels.length === 0 && (
                    <SelectEmpty>该渠道还没有选中的模型（请先在渠道页选择）</SelectEmpty>
                  )}
                  {providerModels.map((m) => (
                    <SelectItem key={m} value={m}>
                      {m}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* 更多设置：价格与接口地址等，默认收起 */}
          <div className="rounded-md border">
            <button
              type="button"
              className="flex w-full items-center justify-between px-3 py-2 text-sm transition-colors hover:bg-accent/40"
              onClick={() => setMoreOpen((open) => !open)}
            >
              <span className="flex items-center gap-2 font-medium">
                更多设置
                {capabilities?.reasoning === false && <Badge variant="outline">未声明推理能力</Badge>}
                {priceSource === "unknown" && <Badge variant="warning">未匹配到价格</Badge>}
              </span>
              <ChevronDown className={cn("h-4 w-4 transition-transform", moreOpen && "rotate-180")} />
            </button>
            <div
              className={cn(
                "overflow-hidden transition-all duration-300",
                moreOpen ? "max-h-[520px] opacity-100" : "max-h-0 opacity-0"
              )}
            >
              <div className="grid gap-3 border-t p-3 lg:grid-cols-2">
                <div className="space-y-1 lg:col-span-2">
                  <Label className="flex h-5 items-center gap-1">API 模型名（自动匹配）</Label>
                  <Input value={apiModelName} readOnly placeholder="选择模型后自动填入" className="font-mono" />
                </div>
                <div className="space-y-1">
                  <Label className="flex h-5 items-center gap-1">
                    输入价（$/M tokens）
                    <HelpTip text="留空表示按目录自动匹配（models.dev / LiteLLM）；手动填写优先" />
                  </Label>
                  <Input value={priceIn} onChange={(e) => setPriceIn(e.target.value)} placeholder="自动匹配" />
                </div>
                <div className="space-y-1">
                  <Label className="flex h-5 items-center gap-1">输出价（$/M tokens）</Label>
                  <Input value={priceOut} onChange={(e) => setPriceOut(e.target.value)} placeholder="自动匹配" />
                </div>
                <div className="space-y-1">
                  <Label className="flex h-5 items-center gap-1">缓存读取价（$/M tokens）</Label>
                  <Input value={priceCachedIn} onChange={(e) => setPriceCachedIn(e.target.value)} placeholder="自动匹配" />
                </div>
                <div className="space-y-1">
                  <Label className="flex h-5 items-center gap-1">缓存写入价（$/M tokens）</Label>
                  <Input value={priceCachedWrite} onChange={(e) => setPriceCachedWrite(e.target.value)} placeholder="自动匹配" />
                </div>
                <div className="space-y-1 lg:col-span-2">
                  <Label className="flex h-5 items-center gap-1">接口地址覆盖</Label>
                  <Input value={endpointOverride} onChange={(e) => setEndpointOverride(e.target.value)} placeholder="可选，覆盖渠道默认地址" />
                </div>
                <div className="space-y-3 rounded-md border bg-muted/20 p-3 lg:col-span-2">
                  <div className="flex items-center gap-1.5 text-sm font-medium">
                    推理配置
                    <HelpTip text="填写任意一项即随部署一并创建一份推理配置；全部留空则只创建部署" />
                  </div>
                  <ProfileFields
                    draft={profileDraft}
                    onChange={(patch) => setProfileDraft((current) => ({ ...current, ...patch }))}
                    capabilities={capabilities}
                  />
                </div>
                {(priceSource !== null || capabilities !== null) && (
                  <p className="text-xs text-muted-foreground lg:col-span-2">
                    {priceSource === "unknown"
                      ? "价格：未匹配到，请手动填写"
                      : `价格来源：${labelForPriceSource(priceSource)}`}
                    {capabilities?.output_limit ? ` · 建议最大输出不超过 ${capabilities.output_limit}` : ""}
                    {capabilities?.context_limit ? ` · 上下文上限 ${capabilities.context_limit}` : ""}
                  </p>
                )}
              </div>
            </div>
          </div>

          <div>
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
                  <TableCell className="truncate font-medium" title={d.name}>{d.name}</TableCell>
                  <TableCell className="truncate" title={d.model_display_name ?? ""}>{d.model_display_name}</TableCell>
                  <TableCell className="truncate" title={d.provider_name ?? ""}>{d.provider_name}</TableCell>
                  <TableCell className="truncate font-mono text-xs" title={d.api_model_name}>{d.api_model_name}</TableCell>
                  <TableCell>
                    <CompactList
                      items={profiles
                        .filter((p) => p.deployment_id === d.id)
                        .map((p) => `${p.name} · ${describeProfile(p)}`)}
                      emptyText="未配置"
                    />
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{fmtTime(d.created_at)}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
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

      <DeploymentEditDialog
        deployment={editing}
        profiles={profiles.filter((p) => p.deployment_id === editing?.id)}
        onClose={() => setEditing(null)}
        onSaved={onChanged}
      />
      {confirmElement}
    </div>
  );
}

function DeploymentEditDialog({
  deployment,
  profiles,
  onClose,
  onSaved,
}: {
  deployment: Deployment | null;
  profiles: ReasoningProfile[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [apiModelName, setApiModelName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [priceIn, setPriceIn] = useState("");
  const [priceOut, setPriceOut] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<PricingPreviewCapabilities | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ProfileDraft>>({});
  const [newDraft, setNewDraft] = useState<ProfileDraft | null>(null);
  const { confirm, confirmElement } = useConfirm();

  useEffect(() => {
    setName(deployment?.name ?? "");
    setApiModelName(deployment?.api_model_name ?? "");
    setEndpoint(deployment?.endpoint_override ?? "");
    setPriceIn(deployment?.price_input_per_mtok?.toString() ?? "");
    setPriceOut(deployment?.price_output_per_mtok?.toString() ?? "");
    setError(null);
  }, [deployment]);

  // 推理配置草稿：打开对话框时按当前 profile 初始化
  useEffect(() => {
    setDrafts(Object.fromEntries(profiles.map((p) => [p.id, draftFromProfile(p)])));
    setNewDraft(null);
  }, [profiles]);

  // 该部署模型的推理能力（用于调整可选项）
  useEffect(() => {
    if (!deployment) return;
    setCapabilities(null);
    void (async () => {
      try {
        const preview = await api.get<PricingPreview>(
          `/api/pricing/preview?provider_id=${encodeURIComponent(deployment.provider_id)}&api_model_name=${encodeURIComponent(deployment.api_model_name)}`
        );
        setCapabilities(preview.capabilities);
      } catch {
        // 能力查询失败时不做限制
      }
    })();
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

  async function saveProfile(profileId: string) {
    const draft = drafts[profileId];
    if (!draft) return;
    setError(null);
    try {
      await api.patch(`/api/reasoning-profiles/${profileId}`, draftToProfilePayload(draft));
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function removeProfile(profileId: string) {
    const ok = await confirm({
      title: "删除推理配置？",
      description: "删除后该配置不再可用于新的评测；历史 Run 已冻结的配置快照不受影响。",
      confirmText: "删除",
    });
    if (!ok) return;
    setError(null);
    try {
      await api.delete(`/api/reasoning-profiles/${profileId}`);
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function createProfileInDialog() {
    if (!deployment || !newDraft) return;
    setError(null);
    try {
      await api.post("/api/reasoning-profiles", {
        deployment_id: deployment.id,
        ...draftToProfilePayload(newDraft),
      });
      setNewDraft(null);
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Dialog open={deployment !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
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
          <div className="space-y-3 rounded-md border bg-muted/20 p-3">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-sm font-medium">
                推理配置
                <HelpTip text="同一部署可以有多份推理配置，作为不同的评测目标；历史 Run 的配置快照不受修改影响" />
              </span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setNewDraft(newDraft ? null : EMPTY_PROFILE_DRAFT)}
                disabled={newDraft !== null}
              >
                + 添加配置
              </Button>
            </div>
            {profiles.length === 0 && newDraft === null && (
              <p className="text-xs text-muted-foreground">该部署暂无推理配置</p>
            )}
            {profiles.map((profile) => {
              const draft = drafts[profile.id] ?? draftFromProfile(profile);
              return (
                <div key={profile.id} className="space-y-3 rounded-md border bg-background p-3">
                  <ProfileFields
                    draft={draft}
                    onChange={(patch) =>
                      setDrafts((current) => ({ ...current, [profile.id]: { ...draft, ...patch } }))
                    }
                    capabilities={capabilities}
                  />
                  <div className="flex justify-end gap-2">
                    <Button size="sm" variant="destructive" onClick={() => void removeProfile(profile.id)}>
                      删除
                    </Button>
                    <Button size="sm" onClick={() => void saveProfile(profile.id)}>
                      保存配置
                    </Button>
                  </div>
                </div>
              );
            })}
            {newDraft && (
              <div className="space-y-3 rounded-md border border-dashed bg-background p-3">
                <ProfileFields
                  draft={newDraft}
                  onChange={(patch) => setNewDraft((current) => (current ? { ...current, ...patch } : current))}
                  capabilities={capabilities}
                />
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="ghost" onClick={() => setNewDraft(null)}>
                    取消
                  </Button>
                  <Button size="sm" onClick={() => void createProfileInDialog()}>
                    添加
                  </Button>
                </div>
              </div>
            )}
          </div>
          <ErrorText error={error} />
          <Button onClick={save} disabled={!name || !apiModelName}>
            保存
          </Button>
        </div>
        {confirmElement}
      </DialogContent>
    </Dialog>
  );
}
