export async function getProvidersSummary() {
  const res = await fetch(`/api/providers/summary`);
  if (!res.ok) throw new Error('Failed to load providers summary');
  return res.json();
}

export async function fetchModels(payload: { name?: string; base_url?: string; api?: string; api_key?: string }) {
  const res = await fetch(`/api/providers/fetch-models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function getDiscoveredModels(name: string) {
  const res = await fetch(`/api/providers/${encodeURIComponent(name)}/discovered-models`);
  if (!res.ok) throw new Error('Failed to load discovered models');
  return res.json();
}

export async function testProvider(payload: { name?: string; base_url?: string; api?: string; api_key?: string }) {
  const res = await fetch(`/api/providers/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function updateModelMeta(name: string, modelId: string, meta: Record<string, any>) {
  const res = await fetch(`/api/providers/${encodeURIComponent(name)}/models/${encodeURIComponent(modelId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(meta),
  });
  if (!res.ok) throw new Error('Failed to update model');
  return res.json();
}
