import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { api, type Deployment, type ModelInfo, type Provider, type ProviderType, type ReasoningProfile } from "@/lib/api";
import { fmtTime } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/HelpTip";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function ErrorText({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="text-sm text-destructive">{error}</p>;
}

export function SetupPage() {
  const [providerTypes, setProviderTypes] = useState<ProviderType[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [profiles, setProfiles] = useState<ReasoningProfile[]>([]);
  const [error, setError] = useState<string | null>(null);

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
      <Tabs defaultValue="providers">
        <TabsList>
          <TabsTrigger value="providers">Provider</TabsTrigger>
          <TabsTrigger value="deployments">Deployment</TabsTrigger>
        </TabsList>
        <TabsContent value="providers">
          <ProvidersTab providerTypes={providerTypes} providers={providers} onChanged={reload} />
        </TabsContent>
        <TabsContent value="deployments">
          <DeploymentsTab
            providers={providers}
            models={models}
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
  onChanged,
}: {
  providerTypes: ProviderType[];
  providers: Provider[];
  onChanged: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState("mock");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [remoteModels, setRemoteModels] = useState<{ provider: Provider; models: string[] } | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Provider | null>(null);

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

  async function fetchModels(provider: Provider) {
    setFetchError(null);
    try {
      const data = await api.get<{ models: string[] }>(`/api/providers/${provider.id}/models`);
      setRemoteModels({ provider, models: data.models });
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e));
    }
  }

  async function deleteProvider(provider: Provider) {
    if (!window.confirm(`删除 Provider「${provider.name}」？其 API Key 会一并从系统安全存储移除。`)) return;
    setListError(null);
    try {
      await api.delete(`/api/providers/${provider.id}`);
      await onChanged();
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    }
  }

  async function addDeploymentFromRemote(provider: Provider, apiModelName: string) {
    // 自动创建 canonical Model（幂等）并创建 Deployment
    const model = await api.post<ModelInfo>("/api/models", {
      canonical_id: apiModelName,
      display_name: apiModelName,
    });
    await api.post("/api/deployments", {
      name: `${apiModelName} · ${provider.name}`,
      model_id: model.id,
      provider_id: provider.id,
      api_model_name: apiModelName,
    });
    await onChanged();
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>添加 Provider</CardTitle>
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
            <Label>Base URL{selectedType?.base_url ? `（默认 ${selectedType.base_url}）` : ""}</Label>
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
          <CardTitle>已有 Provider</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorText error={fetchError ?? listError} />
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>凭据</TableHead>
                <TableHead className="w-[220px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {providers.map((p) => (
                <TableRow key={p.id}>
                  <TableCell>{p.name}</TableCell>
                  <TableCell>{p.type}</TableCell>
                  <TableCell>{p.has_credential ? <Badge variant="success">已保存</Badge> : <Badge variant="outline">未设置</Badge>}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button size="sm" variant="outline" onClick={() => fetchModels(p)}>
                        拉取模型
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

      <Dialog open={remoteModels !== null} onOpenChange={(open) => !open && setRemoteModels(null)}>
        <DialogContent className="max-h-[80vh] overflow-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-1.5">
              从 {remoteModels?.provider.name} 获取的模型
              <HelpTip text="选择一个模型创建 Deployment（同时自动建立 canonical Model）" />
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-1">
            {remoteModels?.models.map((m) => (
              <div key={m} className="flex items-center justify-between rounded border px-3 py-1.5 text-sm">
                <span className="font-mono">{m}</span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    await addDeploymentFromRemote(remoteModels.provider, m);
                    setRemoteModels(null);
                  }}
                >
                  添加
                </Button>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>

      <ProviderEditDialog provider={editing} onClose={() => setEditing(null)} onSaved={onChanged} />
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
          <DialogTitle>编辑 Provider</DialogTitle>
          <DialogDescription>凭据状态：{provider?.has_credential ? "已保存 API Key" : "未设置 API Key"}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>Base URL</Label>
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

// ---------------------------------------------------------------------------
// Deployment 标签页
// ---------------------------------------------------------------------------

function DeploymentsTab({
  providers,
  models,
  deployments,
  profiles,
  onChanged,
}: {
  providers: Provider[];
  models: ModelInfo[];
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
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const [profileFor, setProfileFor] = useState<string | null>(null);
  const [profileName, setProfileName] = useState("");
  const [profileEffort, setProfileEffort] = useState("");
  const [profileTemp, setProfileTemp] = useState("");
  const [editing, setEditing] = useState<Deployment | null>(null);

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
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function deleteDeployment(d: Deployment) {
    if (!window.confirm(`删除 Deployment「${d.name}」？`)) return;
    setListError(null);
    try {
      await api.delete(`/api/deployments/${d.id}`);
      await onChanged();
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    }
  }

  async function deleteProfile(p: ReasoningProfile) {
    if (!window.confirm(`删除 Reasoning Profile「${p.name}」？`)) return;
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
        reasoning_effort: profileEffort || null,
        temperature: profileTemp ? Number(profileTemp) : null,
      });
      setProfileFor(null);
      setProfileName("");
      setProfileEffort("");
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
          <CardTitle>手动创建 Deployment</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 lg:grid-cols-3">
          <div className="space-y-1">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：Kimi K3 · Official" />
          </div>
          <div className="space-y-1">
            <Label>Model</Label>
            <Select value={modelId} onValueChange={setModelId}>
              <SelectTrigger>
                <SelectValue placeholder="选择模型" />
              </SelectTrigger>
              <SelectContent>
                {models.map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Provider</Label>
            <Select value={providerId} onValueChange={setProviderId}>
              <SelectTrigger>
                <SelectValue placeholder="选择渠道" />
              </SelectTrigger>
              <SelectContent>
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
              <HelpTip text="手动价格 override，优先级高于 models.dev 与 LiteLLM 价格目录" />
            </Label>
            <Input value={priceIn} onChange={(e) => setPriceIn(e.target.value)} placeholder="可选" />
          </div>
          <div className="space-y-1">
            <Label>
              输出价（$/M tokens）
              <HelpTip text="手动价格 override，优先级高于 models.dev 与 LiteLLM 价格目录" />
            </Label>
            <Input value={priceOut} onChange={(e) => setPriceOut(e.target.value)} placeholder="可选" />
          </div>
          <div className="lg:col-span-3">
            <ErrorText error={error} />
            <Button onClick={createDeployment} disabled={!name || !modelId || !providerId || !apiModelName}>
              创建 Deployment
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Deployment 列表</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorText error={listError} />
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Provider</TableHead>
                <TableHead>API 模型名</TableHead>
                <TableHead>Reasoning Profiles</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="w-[190px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {deployments.map((d) => (
                <TableRow key={d.id}>
                  <TableCell className="font-medium">{d.name}</TableCell>
                  <TableCell>{d.model_display_name}</TableCell>
                  <TableCell>{d.provider_name}</TableCell>
                  <TableCell className="font-mono text-xs">{d.api_model_name}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {profiles
                        .filter((p) => p.deployment_id === d.id)
                        .map((p) => (
                          <Badge key={p.id} variant="secondary" className="gap-1">
                            {p.name}
                            <button
                              type="button"
                              className="opacity-60 hover:opacity-100"
                              onClick={() => deleteProfile(p)}
                              title="删除该 profile"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </Badge>
                        ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmtTime(d.created_at)}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button size="sm" variant="outline" onClick={() => setProfileFor(d.id)}>
                        + Profile
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
              新建 Reasoning Profile
              <HelpTip text="同一 Deployment 的不同思考强度是独立的评测配置" />
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>名称</Label>
              <Input value={profileName} onChange={(e) => setProfileName(e.target.value)} placeholder="low / medium / high" />
            </div>
            <div className="space-y-1">
              <Label>reasoning_effort</Label>
              <Input value={profileEffort} onChange={(e) => setProfileEffort(e.target.value)} placeholder="可选" />
            </div>
            <div className="space-y-1">
              <Label>temperature</Label>
              <Input value={profileTemp} onChange={(e) => setProfileTemp(e.target.value)} placeholder="可选" />
            </div>
            <Button onClick={createProfile} disabled={!profileName}>
              创建
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <DeploymentEditDialog deployment={editing} onClose={() => setEditing(null)} onSaved={onChanged} />
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
          <DialogTitle>编辑 Deployment</DialogTitle>
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
            <Label>Endpoint override</Label>
            <Input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="可选" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>
                输入价（$/M tokens）
                <HelpTip text="手动价格 override，优先级高于 models.dev 与 LiteLLM 价格目录" />
              </Label>
              <Input value={priceIn} onChange={(e) => setPriceIn(e.target.value)} placeholder="可选" />
            </div>
            <div className="space-y-1">
              <Label>
                输出价（$/M tokens）
                <HelpTip text="手动价格 override，优先级高于 models.dev 与 LiteLLM 价格目录" />
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
