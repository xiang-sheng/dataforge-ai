import { useState } from 'react';
import { Send, Loader2, Lightbulb, Zap, Search } from 'lucide-react';
import { api } from '../api';

const TABS = [
  { key: 'generate', label: '生成 SQL', icon: Lightbulb },
  { key: 'explain', label: '解释 SQL', icon: Search },
  { key: 'optimize', label: '优化 SQL', icon: Zap },
];

const DIALECTS = ['clickhouse', 'postgresql', 'mysql', 'snowflake', 'bigquery', 'redshift', 'doris', 'starrocks', 'spark_sql', 'hive'];

export default function SqlQuery() {
  const [tab, setTab] = useState('generate');
  const [prompt, setPrompt] = useState('');
  const [sql, setSql] = useState('');
  const [dialect, setDialect] = useState('clickhouse');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const handleGenerate = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const r = await api.generateSql({ prompt, dialect });
      setSql(r.sql);
      setResult(r);
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  const handleExplain = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const r = await api.explainSql({ sql, dialect, detail_level: 'detailed' });
      setResult(r);
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  const handleOptimize = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const r = await api.optimizeSql({ sql, dialect });
      setResult(r);
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  const actions = { generate: handleGenerate, explain: handleExplain, optimize: handleOptimize };

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">智能问数</h1>
        <p className="text-sm text-gray-500 mt-1">用自然语言生成 SQL、解释查询逻辑、优化性能</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-5 bg-gray-100 rounded-lg p-1 w-fit">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button key={key} onClick={() => { setTab(key); setResult(null); setError(null); }}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm rounded-md transition ${tab === key ? 'bg-white text-gray-900 shadow-sm font-medium' : 'text-gray-500 hover:text-gray-700'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {/* Input area */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-5">
        <div className="flex items-center gap-3 mb-3">
          <label className="text-xs font-medium text-gray-500">方言</label>
          <select value={dialect} onChange={e => setDialect(e.target.value)}
            className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-indigo-400">
            {DIALECTS.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>

        {tab === 'generate' ? (
          <div>
            <textarea value={prompt} onChange={e => setPrompt(e.target.value)}
              placeholder="用自然语言描述你的查询需求，例如：统计 2025 年 6 月各商品类别的销售总额，按金额降序排列"
              className="w-full h-28 p-3 text-sm border border-gray-200 rounded-lg resize-none outline-none focus:border-indigo-400" />
          </div>
        ) : (
          <div>
            <textarea value={sql} onChange={e => setSql(e.target.value)}
              placeholder="输入 SQL 语句..."
              className="w-full h-36 p-3 text-sm font-mono border border-gray-200 rounded-lg resize-none outline-none focus:border-indigo-400" />
          </div>
        )}

        <div className="flex justify-end mt-3">
          <button onClick={actions[tab]} disabled={loading}
            className="flex items-center gap-1.5 px-5 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 transition disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {tab === 'generate' ? '生成' : tab === 'explain' ? '解释' : '优化'}
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl p-4 mb-5">{error}</div>
      )}

      {/* Result */}
      {result && (
        <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-4">
          {tab === 'generate' && (
            <>
              <div>
                <div className="text-xs font-medium text-gray-500 mb-2">生成的 SQL</div>
                <pre className="bg-gray-50 border border-gray-100 rounded-lg p-4 text-sm font-mono text-gray-800 overflow-x-auto whitespace-pre-wrap">{result.sql}</pre>
              </div>
              {result.explanation && (
                <div>
                  <div className="text-xs font-medium text-gray-500 mb-1">说明</div>
                  <p className="text-sm text-gray-700">{result.explanation}</p>
                </div>
              )}
              {result.warnings?.length > 0 && (
                <div className="text-xs text-amber-600 bg-amber-50 rounded-lg p-3">
                  {result.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
                </div>
              )}
            </>
          )}

          {tab === 'explain' && (
            <>
              <div className="text-sm font-medium text-gray-900">{result.summary}</div>
              <div className="space-y-1.5">
                {result.step_by_step?.map((s, i) => (
                  <div key={i} className="flex gap-2 text-sm text-gray-700">
                    <span className="flex-shrink-0 w-5 h-5 bg-indigo-100 text-indigo-700 rounded-full flex items-center justify-center text-xs font-bold">{i+1}</span>
                    {s}
                  </div>
                ))}
              </div>
              <div className="flex gap-4 text-xs text-gray-500">
                <span>复杂度: <strong className="text-gray-700">{result.estimated_complexity}</strong></span>
                {result.tables_used?.length > 0 && <span>涉及表: {result.tables_used.join(', ')}</span>}
              </div>
            </>
          )}

          {tab === 'optimize' && (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-medium text-gray-500 mb-2">原始 SQL</div>
                  <pre className="bg-gray-50 border border-gray-100 rounded-lg p-3 text-xs font-mono text-gray-600 overflow-x-auto whitespace-pre-wrap">{result.original_sql}</pre>
                </div>
                <div>
                  <div className="text-xs font-medium text-green-600 mb-2">优化后</div>
                  <pre className="bg-green-50 border border-green-100 rounded-lg p-3 text-xs font-mono text-green-800 overflow-x-auto whitespace-pre-wrap">{result.optimized_sql}</pre>
                </div>
              </div>
              {result.changes?.length > 0 && (
                <div className="text-sm text-gray-700">
                  <div className="text-xs font-medium text-gray-500 mb-1">变更说明</div>
                  {result.changes.map((c, i) => <div key={i} className="flex gap-1"><span className="text-indigo-500">•</span>{c}</div>)}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
