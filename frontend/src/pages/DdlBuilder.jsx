import { useState } from 'react';
import { Hammer, Loader2, CheckCircle, XCircle, FileCode } from 'lucide-react';
import { api } from '../api';

const LAYERS = ['ODS', 'DWD', 'DWS', 'ADS'];
const DB_TYPES = ['clickhouse', 'hive', 'doris', 'mysql', 'postgresql', 'duckdb'];

export default function DdlBuilder() {
  const [mode, setMode] = useState('agent'); // 'agent' or 'pipeline'
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Agent mode
  const [sourceTable, setSourceTable] = useState('');
  const [targetLayer, setTargetLayer] = useState('ODS');
  const [businessDesc, setBusinessDesc] = useState('');
  const [dbPath, setDbPath] = useState('');

  // Pipeline mode
  const [schemaJson, setSchemaJson] = useState('');
  const [targetDbType, setTargetDbType] = useState('clickhouse');
  const [localVerify, setLocalVerify] = useState(true);

  const handleAgentBuild = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const r = await api.chat({
        message: `为源表 ${sourceTable} 生成 ${targetLayer} 层 DDL。${businessDesc ? `业务描述：${businessDesc}` : ''}`,
        target_agent: 'ddl_build',
        db_path: dbPath || undefined,
      });
      setResult({ mode: 'agent', ...r });
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  const handlePipelineBuild = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const schemas = JSON.parse(schemaJson);
      const r = await api.buildDdl({
        source_schemas: schemas,
        target_layer: targetLayer,
        target_db_type: targetDbType,
        local_verify: localVerify,
      });
      setResult({ mode: 'pipeline', ...r });
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-900">DDL 建模</h1>
          <p className="text-sm text-gray-500 mt-1">从源表结构自动生成数仓 DDL，支持 ODS / DWD / DWS / ADS 分层</p>
        </div>
        <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
          <button onClick={() => setMode('agent')} className={`px-3 py-1.5 text-sm rounded-md transition ${mode === 'agent' ? 'bg-white shadow-sm font-medium text-gray-900' : 'text-gray-500'}`}>Agent 模式</button>
          <button onClick={() => setMode('pipeline')} className={`px-3 py-1.5 text-sm rounded-md transition ${mode === 'pipeline' ? 'bg-white shadow-sm font-medium text-gray-900' : 'text-gray-500'}`}>Pipeline 模式</button>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-5">
        {mode === 'agent' ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <Field label="源表名称"><input className="input" value={sourceTable} onChange={e => setSourceTable(e.target.value)} placeholder="orders" /></Field>
              <Field label="目标层级">
                <select className="input" value={targetLayer} onChange={e => setTargetLayer(e.target.value)}>
                  {LAYERS.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </Field>
            </div>
            <Field label="业务描述（可选）"><input className="input" value={businessDesc} onChange={e => setBusinessDesc(e.target.value)} placeholder="订单事实表，记录购买行为" /></Field>
            <Field label="DuckDB 路径（可选）"><input className="input" value={dbPath} onChange={e => setDbPath(e.target.value)} placeholder=":memory:" /></Field>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-4">
              <Field label="目标层级">
                <select className="input" value={targetLayer} onChange={e => setTargetLayer(e.target.value)}>
                  {LAYERS.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </Field>
              <Field label="目标引擎">
                <select className="input" value={targetDbType} onChange={e => setTargetDbType(e.target.value)}>
                  {DB_TYPES.map(d => <option key={d} value={d}>{d}</option>)}
                </select>
              </Field>
              <Field label="本地验证">
                <label className="flex items-center gap-2 mt-1">
                  <input type="checkbox" checked={localVerify} onChange={e => setLocalVerify(e.target.checked)} className="rounded" />
                  <span className="text-sm text-gray-600">DuckDB 沙箱验证</span>
                </label>
              </Field>
            </div>
            <Field label="源表 Schema（JSON 数组）">
              <textarea value={schemaJson} onChange={e => setSchemaJson(e.target.value)}
                placeholder='[{"table_name": "orders", "columns": [{"name": "id", "data_type": "BIGINT"}, {"name": "amount", "data_type": "DECIMAL"}]}]'
                className="input font-mono h-32 resize-none" />
            </Field>
          </div>
        )}

        <div className="flex justify-end mt-4">
          <button onClick={mode === 'agent' ? handleAgentBuild : handlePipelineBuild} disabled={loading}
            className="flex items-center gap-1.5 px-5 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 transition disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Hammer className="w-4 h-4" />}
            生成 DDL
          </button>
        </div>
      </div>

      {error && <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl p-4 mb-5">{error}</div>}

      {result && (
        <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-4">
          {result.mode === 'agent' ? (
            <>
              <div className="flex items-center gap-2">
                {result.success ? <CheckCircle className="w-4 h-4 text-green-500" /> : <XCircle className="w-4 h-4 text-red-500" />}
                <span className="text-sm font-medium text-gray-900">Agent: {result.agent_name}</span>
              </div>
              <pre className="bg-gray-50 border border-gray-100 rounded-lg p-4 text-sm font-mono text-gray-800 overflow-x-auto whitespace-pre-wrap">{result.content}</pre>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <FileCode className="w-4 h-4 text-indigo-500" />
                <span className="text-sm font-medium text-gray-900">Pipeline 结果</span>
              </div>
              {result.tables?.map((t, i) => (
                <div key={i} className="border border-gray-100 rounded-lg p-4">
                  <div className="text-sm font-medium text-gray-800 mb-2">{t.source_table} → {t.target_table}</div>
                  <pre className="bg-gray-50 rounded-lg p-3 text-xs font-mono text-gray-700 overflow-x-auto whitespace-pre-wrap">{t.ddl}</pre>
                  {t.computation_sql && (
                    <div className="mt-3">
                      <div className="text-xs font-medium text-gray-500 mb-1">计算 SQL</div>
                      <pre className="bg-gray-50 rounded-lg p-3 text-xs font-mono text-gray-600 overflow-x-auto whitespace-pre-wrap">{t.computation_sql}</pre>
                    </div>
                  )}
                </div>
              ))}
              <pre className="bg-gray-50 border border-gray-100 rounded-lg p-3 text-xs font-mono text-gray-600 overflow-x-auto whitespace-pre-wrap max-h-60">{JSON.stringify(result, null, 2)}</pre>
            </>
          )}
        </div>
      )}

      <style>{`.input { width: 100%; padding: 8px 12px; border: 1px solid #e5e7eb; border-radius: 8px; font-size: 14px; outline: none; transition: border-color 0.2s; } .input:focus { border-color: #6366f1; }`}</style>
    </div>
  );
}

function Field({ label, children }) {
  return <label className="block"><span className="block text-xs font-medium text-gray-600 mb-1.5">{label}</span>{children}</label>;
}
