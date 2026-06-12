import { useState } from 'react';
import { Shield, Loader2, AlertTriangle, GitMerge, Search } from 'lucide-react';
import { api } from '../api';

export default function Governance() {
  const [mode, setMode] = useState('chat');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Chat mode
  const [message, setMessage] = useState('');
  const [dbPath, setDbPath] = useState('');

  // Direct scan (via chat agent with governance target)
  const handleScan = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const r = await api.chat({
        message: message || '请对当前数据库做数据治理分析，扫描冗余表并给出治理建议。',
        target_agent: 'data_governance',
        db_path: dbPath || undefined,
      });
      setResult(r);
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">数据治理</h1>
        <p className="text-sm text-gray-500 mt-1">Embedding 预筛 + LLM 深度分析，识别冗余表和重叠结构，给出合并/归档建议</p>
      </div>

      {/* How it works */}
      <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-4 mb-6">
        <div className="flex items-center gap-2 text-sm font-medium text-indigo-900 mb-2">
          <Search className="w-4 h-4" /> 工作流程
        </div>
        <div className="grid grid-cols-4 gap-3 text-xs text-indigo-700">
          <Step n={1} title="Embedding 预筛" desc="全库 schema 向量化，余弦相似度筛选候选表对" />
          <Step n={2} title="LLM 深度验证" desc="对高相似度候选对逐一做结构和数据对比" />
          <Step n={3} title="数据抽样" desc="抽查样本数据确认是否真的重叠" />
          <Step n={4} title="治理报告" desc="输出冗余清单 + 合并/归档/删除建议" />
        </div>
      </div>

      {/* Input */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-5">
        <div className="space-y-4">
          <Field label="分析需求">
            <textarea value={message} onChange={e => setMessage(e.target.value)}
              placeholder="例如：扫描所有表，找出冗余表并给出治理建议。也可以描述具体需求。"
              className="input h-24 resize-none" />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="DuckDB 路径（可选）">
              <input className="input" value={dbPath} onChange={e => setDbPath(e.target.value)} placeholder=":memory:" />
            </Field>
          </div>
        </div>

        <div className="flex justify-end mt-4">
          <button onClick={handleScan} disabled={loading}
            className="flex items-center gap-1.5 px-5 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 transition disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />}
            开始治理分析
          </button>
        </div>
      </div>

      {error && <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl p-4 mb-5">{error}</div>}

      {result && (
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <div className="flex items-center gap-2 mb-4">
            {result.success
              ? <div className="w-8 h-8 bg-green-100 rounded-lg flex items-center justify-center"><AlertTriangle className="w-4 h-4 text-green-600" /></div>
              : <div className="w-8 h-8 bg-red-100 rounded-lg flex items-center justify-center"><AlertTriangle className="w-4 h-4 text-red-600" /></div>
            }
            <div>
              <div className="text-sm font-medium text-gray-900">治理分析报告</div>
              <div className="text-xs text-gray-500">Agent: {result.agent_name} · {result.metadata?.tool_calls_count || 0} 次工具调用</div>
            </div>
          </div>
          <div className="prose prose-sm max-w-none">
            <pre className="bg-gray-50 border border-gray-100 rounded-lg p-4 text-sm text-gray-800 overflow-x-auto whitespace-pre-wrap font-sans leading-relaxed">{result.content}</pre>
          </div>
        </div>
      )}

      <style>{`.input { width: 100%; padding: 8px 12px; border: 1px solid #e5e7eb; border-radius: 8px; font-size: 14px; outline: none; transition: border-color 0.2s; } .input:focus { border-color: #6366f1; }`}</style>
    </div>
  );
}

function Step({ n, title, desc }) {
  return (
    <div className="bg-white rounded-lg p-3 border border-indigo-100">
      <div className="w-5 h-5 bg-indigo-600 text-white rounded-full flex items-center justify-center text-xs font-bold mb-1.5">{n}</div>
      <div className="font-medium text-indigo-900 mb-0.5">{title}</div>
      <div className="text-indigo-600 leading-relaxed">{desc}</div>
    </div>
  );
}

function Field({ label, children }) {
  return <label className="block"><span className="block text-xs font-medium text-gray-600 mb-1.5">{label}</span>{children}</label>;
}
