import { cachedUserId, http } from "./core";

export interface DataSource {
  id: string;
  user_id: string;
  name: string;
  type: string;
  status: string;
  config: Record<string, unknown>;
  capabilities: string[];
  last_sync_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface DataSourceCreate {
  name: string;
  type: string;
  config: Record<string, unknown>;
}

export interface DataSourceUpdate {
  name?: string;
  status?: string;
  config?: Record<string, unknown>;
}

export async function listDataSources(): Promise<DataSource[]> {
  return http<DataSource[]>(`/api/data-sources?user_id=${cachedUserId}`);
}

export async function createDataSource(data: DataSourceCreate): Promise<DataSource> {
  return http<DataSource>(`/api/data-sources?user_id=${cachedUserId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function updateDataSource(id: string, data: DataSourceUpdate): Promise<DataSource> {
  return http<DataSource>(`/api/data-sources/${id}?user_id=${cachedUserId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function deleteDataSource(id: string): Promise<void> {
  await http<void>(`/api/data-sources/${id}?user_id=${cachedUserId}`, { method: "DELETE" });
}

export async function syncDataSource(id: string): Promise<void> {
  await http<void>(`/api/data-sources/${id}/sync?user_id=${cachedUserId}`, { method: "POST" });
}

export async function pauseDataSource(id: string): Promise<void> {
  await http<void>(`/api/data-sources/${id}/pause?user_id=${cachedUserId}`, { method: "POST" });
}

export async function resumeDataSource(id: string): Promise<void> {
  await http<void>(`/api/data-sources/${id}/resume?user_id=${cachedUserId}`, { method: "POST" });
}
