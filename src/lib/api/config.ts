import { http } from "./core";

export type Config = {
  // LLM
  llm_provider: string;
  llm_model: string;
  llm_api_key: string;
  llm_base_url: string;
  has_llm_key: boolean;

  // Embedding
  embedding_provider: string;
  embedding_model: string;
  embedding_api_key: string;
  embedding_base_url: string;
  has_embedding_key: boolean;

  // 旧字段（向后兼容）
  dashscope_api_key: string;
  has_api_key: boolean;
};

export type ConfigTestResponse = {
  ok: boolean;
  latency_ms: number;
  error: string;
};

export async function getConfig(): Promise<Config> {
  return http<Config>("/api/config");
}

export async function updateConfig(data: Partial<Config>): Promise<Config> {
  return http<Config>("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function testConfig(data: {
  provider: string;
  model: string;
  api_key: string;
  base_url?: string;
}): Promise<ConfigTestResponse> {
  return http<ConfigTestResponse>("/api/config/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export type ProviderCatalog = Record<
  string,
  { name: string; baseUrl: string; models: string[]; embeddingModels: string[] }
>;

export async function getProviders(): Promise<ProviderCatalog> {
  return http<ProviderCatalog>("/api/config/providers");
}
