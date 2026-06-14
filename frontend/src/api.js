const BASE = '/api/v1';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Connections
  listConnections: (params) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/connections${qs ? `?${qs}` : ''}`);
  },
  createConnection: (data) =>
    request('/connections', { method: 'POST', body: JSON.stringify(data) }),
  getConnection: (id) => request(`/connections/${id}`),
  updateConnection: (id, data) =>
    request(`/connections/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteConnection: (id) => request(`/connections/${id}`, { method: 'DELETE' }),
  testConnection: (id) => request(`/connections/${id}/test`, { method: 'POST' }),
  listDatabases: (id) => request(`/connections/${id}/databases`),
  listTables: (id, database) => request(`/connections/${id}/tables?database=${encodeURIComponent(database)}`),

  // SQL
  generateSql: (data) => request('/sql/generate', { method: 'POST', body: JSON.stringify(data) }),
  explainSql: (data) => request('/sql/explain', { method: 'POST', body: JSON.stringify(data) }),
  optimizeSql: (data) => request('/sql/optimize', { method: 'POST', body: JSON.stringify(data) }),
  executeSql: (data) => request('/sql/execute', { method: 'POST', body: JSON.stringify(data) }),

  // DDL
  buildDdl: (data) => request('/ddl/build', { method: 'POST', body: JSON.stringify(data) }),
  verifyDdl: (data) => request('/ddl/verify', { method: 'POST', body: JSON.stringify(data) }),

  // Lineage
  getTableLineage: (tableId, params) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/lineage/table/${encodeURIComponent(tableId)}${qs ? `?${qs}` : ''}`);
  },
  getColumnLineage: (tableId, columnName, params) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/lineage/column/${encodeURIComponent(tableId)}/${encodeURIComponent(columnName)}${qs ? `?${qs}` : ''}`);
  },
  analyzeLineage: (data) => request('/lineage/analyze', { method: 'POST', body: JSON.stringify(data) }),

  // Agent
  chat: (data) => request('/agent/chat', { method: 'POST', body: JSON.stringify(data) }),
  listAgents: () => request('/agent/agents'),
  analyze: (data) => request('/agent/analyze', { method: 'POST', body: JSON.stringify(data) }),
};
