import { useState, useEffect } from 'react';
import { Plus, Trash2, RefreshCw, CheckCircle, XCircle, Loader2, Database } from 'lucide-react';
import { api } from '../api';

const DB_TYPES = ['postgresql', 'mysql', 'clickhouse', 'snowflake', 'bigquery', 'redshift', 'doris', 'starrocks'];

const EMPTY_FORM = {
  name: '', db_type: 'postgresql', host: 'localhost', port: 5432,
  username: '', password: '', default_database: '', tags: [],
};

export default function Connections() {
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(null);
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setConnections(await api.listConnections({}));
    } catch (e) {
      setConnections([]);
      setError('Failed to load connections');
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    await api.createConnection(form);
    setForm({ ...EMPTY_FORM });
    setShowForm(false);
    load();
  };

  const handleDelete = async (id) => {
    if (!confirm('确认删除此连接？')) return;
    await api.deleteConnection(id);
    load();
  };

  const handleTest = async (id) => {
    setTesting(id);
    setTestResult(null);
    try {
      const r = await api.testConnection(id);
      setTestResult({ id, ...r });
    } catch (e) {
      setTestResult({ id, success: false, message: e.message });
    }
    setTesting(null);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-900">连接管理</h1>
          <p className="text-sm text-gray-500 mt-1">管理数据库连接，支持 PostgreSQL / MySQL / ClickHouse 等多种数据源</p>
        </div>
        <button onClick={() => setShowForm(!showForm)} className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 transition">
          <Plus className="w-4 h-4" /> 新建连接
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="bg-white border border-gray-200 rounded-xl p-5 mb-6 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <Field label="连接名称"><input required className="input" value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="生产环境-PostgreSQL" /></Field>
            <Field label="数据库类型">
              <select className="input" value={form.db_type} onChange={e => setForm({...form, db_type: e.target.value})}>
                {DB_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="主机"><input required className="input" value={form.host} onChange={e => setForm({...form, host: e.target.value})} /></Field>
            <Field label="端口"><input required type="number" className="input" value={form.port} onChange={e => setForm({...form, port: +e.target.value})} /></Field>
            <Field label="用户名"><input required className="input" value={form.username} onChange={e => setForm({...form, username: e.target.value})} /></Field>
            <Field label="密码"><input required type="password" className="input" value={form.password} onChange={e => setForm({...form, password: e.target.value})} /></Field>
            <Field label="默认数据库"><input className="input" value={form.default_database} onChange={e => setForm({...form, default_database: e.target.value})} /></Field>
          </div>
          <div className="flex gap-2 justify-end">
            <button type="button" onClick={() => setShowForm(false)} className="px-4 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">取消</button>
            <button type="submit" className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">创建</button>
          </div>
        </form>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl p-4 mb-5">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-20"><Loader2 className="w-6 h-6 animate-spin text-indigo-500" /></div>
      ) : connections.length === 0 ? (
        <div className="text-center py-20 text-gray-400">
          <Database className="w-10 h-10 mx-auto mb-3 opacity-50" />
          <p>暂无连接，点击"新建连接"开始</p>
        </div>
      ) : (
        <div className="space-y-3">
          {connections.map(c => (
            <div key={c.id} className="bg-white border border-gray-200 rounded-xl p-4 flex items-center justify-between hover:shadow-sm transition">
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 rounded-lg bg-indigo-50 flex items-center justify-center">
                  <Database className="w-5 h-5 text-indigo-600" />
                </div>
                <div>
                  <div className="font-medium text-gray-900">{c.name}</div>
                  <div className="text-xs text-gray-500 mt-0.5">{c.db_type} · {c.host}:{c.port} · {c.username}</div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {testResult?.id === c.id && (
                  <span className={`flex items-center gap-1 text-xs ${testResult.success ? 'text-green-600' : 'text-red-500'}`}>
                    {testResult.success ? <CheckCircle className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
                    {testResult.success ? `${testResult.latency_ms?.toFixed(0)}ms` : '失败'}
                  </span>
                )}
                <button onClick={() => handleTest(c.id)} disabled={testing === c.id} className="p-2 text-gray-400 hover:text-indigo-600 rounded-lg hover:bg-indigo-50 transition disabled:opacity-50">
                  {testing === c.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                </button>
                <button onClick={() => handleDelete(c.id)} className="p-2 text-gray-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <style>{`.input { width: 100%; padding: 8px 12px; border: 1px solid #e5e7eb; border-radius: 8px; font-size: 14px; outline: none; transition: border-color 0.2s; } .input:focus { border-color: #6366f1; }`}</style>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-gray-600 mb-1.5">{label}</span>
      {children}
    </label>
  );
}
