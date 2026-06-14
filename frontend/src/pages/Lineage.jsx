import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Search, Loader2, AlertCircle, ChevronRight, X, ZoomIn, ZoomOut,
  ArrowRight, ArrowLeftRight, GitBranch, Table2, Layers, Maximize2,
  Play, RotateCcw,
} from 'lucide-react';
import { api } from '../api';

// ======================================================================
// Constants
// ======================================================================

const NODE_W = 184;
const NODE_H = 54;
const LAYER_GAP = 250;
const NODE_GAP = 86;
const PAD_X = 80;
const PAD_Y = 60;

const LAYER_COLORS = {
  ods:  { bg: '#172554', border: '#3b82f6', text: '#93c5fd', label: 'ODS' },
  dwd:  { bg: '#052e16', border: '#22c55e', text: '#86efac', label: 'DWD' },
  dws:  { bg: '#431407', border: '#f97316', text: '#fdba74', label: 'DWS' },
  ads:  { bg: '#450a0a', border: '#ef4444', text: '#fca5a5', label: 'ADS' },
};

const DEFAULT_NODE_STYLE = {
  bg: '#1e293b', border: '#6366f1', text: '#c7d2fe', label: '',
};

const TABS = [
  { key: 'lineage', label: '表血缘追踪', icon: GitBranch },
  { key: 'analyze', label: 'SQL 血缘分析', icon: Search },
];

const DIRECTIONS = [
  { value: 'both',       label: '上下游',   icon: ArrowLeftRight },
  { value: 'upstream',   label: '仅上游',   icon: ArrowRight },
  { value: 'downstream', label: '仅下游',   icon: ArrowRight },
];

const DIALECTS = [
  'clickhouse', 'postgresql', 'mysql', 'snowflake',
  'bigquery', 'redshift', 'doris', 'starrocks', 'hive', 'spark_sql',
];

// ======================================================================
// SVG helpers
// ======================================================================

/** Arrow + glow marker definitions injected once into <defs>. */
function SvgDefs() {
  return (
    <defs>
      <marker
        id="lg-arrow"
        viewBox="0 0 10 8"
        markerWidth="10" markerHeight="8"
        refX="10" refY="4"
        orient="auto"
        markerUnits="userSpaceOnUse"
      >
        <path d="M0,0.5 L9,4 L0,7.5" fill="none" stroke="#475569" strokeWidth="1.2" strokeLinejoin="round" />
      </marker>
      <marker
        id="lg-arrow-hl"
        viewBox="0 0 10 8"
        markerWidth="10" markerHeight="8"
        refX="10" refY="4"
        orient="auto"
        markerUnits="userSpaceOnUse"
      >
        <path d="M0,0.5 L9,4 L0,7.5" fill="none" stroke="#818cf8" strokeWidth="1.2" strokeLinejoin="round" />
      </marker>
      <filter id="lg-glow" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="4" result="b" />
        <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <filter id="lg-shadow" x="-10%" y="-10%" width="120%" height="140%">
        <feDropShadow dx="0" dy="2" stdDeviation="3" floodColor="#000" floodOpacity="0.35" />
      </filter>
    </defs>
  );
}

// ======================================================================
// Layout algorithm  (topological-sort layered DAG)
// ======================================================================

/**
 * Assigns x/y positions to every node using Kahn's algorithm for layer
 * assignment, then centres each layer vertically.
 *
 * @returns {{ positions: Object<string, {x:number,y:number}>, svgW: number, svgH: number }}
 */
function computeLayout(nodes, edges) {
  if (!nodes || nodes.length === 0) {
    return { positions: {}, svgW: 400, svgH: 300 };
  }

  const ids = new Set(nodes.map(n => n.id));

  // Build adjacency (predecessors only)
  const predOf = {};
  nodes.forEach(n => { predOf[n.id] = []; });
  edges.forEach(e => {
    if (ids.has(e.source_id) && ids.has(e.target_id)) {
      predOf[e.target_id].push(e.source_id);
    }
  });

  // Kahn's algorithm — layer = longest-path from any root
  const remaining = new Set(ids);
  const layers = {};
  let assigned = 0;

  // Seed: nodes with zero in-degree
  remaining.forEach(id => {
    if (predOf[id].length === 0) { layers[id] = 0; assigned++; }
  });

  // Safety guard against infinite loops on cyclic data
  let guard = nodes.length + 2;
  while (assigned < nodes.length && guard-- > 0) {
    let progress = false;
    remaining.forEach(id => {
      if (layers[id] !== undefined) return;
      const preds = predOf[id].filter(p => layers[p] !== undefined);
      if (preds.length === predOf[id].length) {
        layers[id] = Math.max(...preds.map(p => layers[p])) + 1;
        assigned++;
        progress = true;
      }
    });
    if (!progress) {
      // Remaining nodes are in a cycle — dump them into the next layer
      const fallback = Math.max(0, ...Object.values(layers)) + 1;
      remaining.forEach(id => { if (layers[id] === undefined) layers[id] = fallback; });
      break;
    }
  }

  // Group by layer
  const buckets = {};
  nodes.forEach(n => {
    const l = layers[n.id] ?? 0;
    (buckets[l] = buckets[l] || []).push(n);
  });

  const maxLayer = Math.max(0, ...Object.keys(buckets).map(Number));

  // Assign pixel positions
  const positions = {};
  let maxH = 0;

  for (let l = 0; l <= maxLayer; l++) {
    const bucket = buckets[l] || [];
    const x = PAD_X + l * LAYER_GAP;
    const totalH = bucket.length * NODE_H + (bucket.length - 1) * NODE_GAP;
    const y0 = PAD_Y + (maxH > 0 ? maxH / 2 - totalH / 2 : 0);

    bucket.forEach((node, i) => {
      positions[node.id] = { x, y: y0 + i * (NODE_H + NODE_GAP) };
    });

    maxH = Math.max(maxH, totalH);
  }

  const svgW = PAD_X * 2 + maxLayer * LAYER_GAP + NODE_W;
  const svgH = PAD_Y * 2 + Math.max(maxH, 200);

  return { positions, svgW, svgH };
}

/**
 * Returns an SVG cubic-bezier `d` string for an edge.
 */
function edgePath(sx, sy, tx, ty) {
  const x1 = sx + NODE_W;
  const y1 = sy + NODE_H / 2;
  const x2 = tx;
  const y2 = ty + NODE_H / 2;
  const dx = Math.abs(x2 - x1);
  const c = Math.max(dx * 0.45, 50);
  return `M${x1},${y1} C${x1 + c},${y1} ${x2 - c},${y2} ${x2},${y2}`;
}

// ======================================================================
// Small sub-components
// ======================================================================

function Spinner() {
  return (
    <div style={S.center}>
      <Loader2 className="w-8 h-8 animate-spin" style={{ color: '#818cf8' }} />
    </div>
  );
}

function ErrorBox({ message, onRetry }) {
  return (
    <div style={S.errorBox}>
      <AlertCircle className="w-5 h-5 flex-shrink-0" style={{ color: '#f87171' }} />
      <div>
        <div style={{ fontWeight: 500, marginBottom: 2 }}>请求失败</div>
        <div style={{ opacity: 0.85 }}>{message}</div>
      </div>
      {onRetry && (
        <button onClick={onRetry} style={S.errorRetry}>
          <RotateCcw className="w-3.5 h-3.5" /> 重试
        </button>
      )}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontSize: 11, fontWeight: 600, color: '#94a3b8', letterSpacing: '0.03em', textTransform: 'uppercase' }}>
        {label}
      </span>
      {children}
    </label>
  );
}

function LayerLegend() {
  return (
    <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
      {Object.entries(LAYER_COLORS).map(([key, c]) => (
        <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <div style={{ width: 10, height: 10, borderRadius: 3, background: c.border }} />
          <span style={{ fontSize: 11, color: '#94a3b8' }}>{c.label}</span>
        </div>
      ))}
    </div>
  );
}

/** Renders a single DAG node inside an SVG <g>. */
function DagNode({
  node, pos, isRoot, isSelected, layerStyle,
  onMouseDown, onClick,
}) {
  return (
    <g
      transform={`translate(${pos.x},${pos.y})`}
      style={{ cursor: 'pointer' }}
      onMouseDown={onMouseDown}
      onClick={onClick}
    >
      {/* Background rect */}
      <rect
        width={NODE_W} height={NODE_H} rx={10} ry={10}
        fill={layerStyle.bg}
        stroke={isSelected ? '#818cf8' : layerStyle.border}
        strokeWidth={isSelected ? 2.5 : 1.5}
        filter={isRoot ? 'url(#lg-glow)' : 'url(#lg-shadow)'}
        opacity={0.95}
      />
      {/* Layer badge */}
      {layerStyle.label && (
        <>
          <rect
            x={8} y={7}
            width={layerStyle.label.length * 7.5 + 14} height={17}
            rx={4} fill={layerStyle.border} opacity={0.18}
          />
          <text
            x={8 + (layerStyle.label.length * 7.5 + 14) / 2} y={19}
            textAnchor="middle"
            fill={layerStyle.text}
            style={{ fontSize: 9, fontWeight: 700, fontFamily: 'system-ui, sans-serif', letterSpacing: '0.06em' }}
          >
            {layerStyle.label}
          </text>
        </>
      )}
      {/* Table name */}
      <text
        x={NODE_W / 2} y={layerStyle.label ? 39 : NODE_H / 2 + 1}
        textAnchor="middle" dominantBaseline="middle"
        fill="#f1f5f9"
        style={{ fontSize: 12.5, fontWeight: 500, fontFamily: 'system-ui, sans-serif' }}
      >
        {node.table_name.length > 20 ? node.table_name.slice(0, 18) + '…' : node.table_name}
      </text>
      {/* Root indicator */}
      {isRoot && (
        <circle cx={NODE_W - 12} cy={12} r={4} fill={layerStyle.border} opacity={0.7} />
      )}
    </g>
  );
}

// ======================================================================
// Main component
// ======================================================================

export default function Lineage() {
  // ---- Tab state ----
  const [activeTab, setActiveTab] = useState('lineage');

  // ---- Lineage form state ----
  const [tableName, setTableName] = useState('');
  const [connectionId, setConnectionId] = useState('');
  const [database, setDatabase] = useState('');
  const [depth, setDepth] = useState(5);
  const [direction, setDirection] = useState('both');

  // ---- Graph state ----
  const [graphData, setGraphData] = useState(null);
  const [layout, setLayout] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // ---- View transform (zoom + pan) ----
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const panStartRef = useRef(null);
  const panOriginRef = useRef(null);

  // ---- Interaction state ----
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [hoveredEdge, setHoveredEdge] = useState(null);

  // ---- Column drill-down ----
  const [columnData, setColumnData] = useState(null);
  const [columnLoading, setColumnLoading] = useState(false);
  const [columnError, setColumnError] = useState(null);

  // ---- SQL Analyze state ----
  const [sqlInput, setSqlInput] = useState('');
  const [sqlDialect, setSqlDialect] = useState('clickhouse');
  const [analyzeResult, setAnalyzeResult] = useState(null);
  const [analyzeLoading, setAnalyzeLoading] = useState(false);
  const [analyzeError, setAnalyzeError] = useState(null);

  // ---- Refs ----
  const svgContainerRef = useRef(null);
  const svgRef = useRef(null);

  // ==================================================================
  // Recompute layout whenever graph data changes
  // ==================================================================
  useEffect(() => {
    if (graphData?.nodes?.length) {
      const l = computeLayout(graphData.nodes, graphData.edges);
      setLayout(l);
      setSelectedNodeId(null);
      closeColumnPanel();
    } else {
      setLayout(null);
    }
  }, [graphData]);

  // ==================================================================
  // Non-passive wheel listener so we can preventDefault on zoom
  // ==================================================================
  useEffect(() => {
    const el = svgContainerRef.current;
    if (!el) return;
    const handler = (e) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const delta = -e.deltaY * 0.002;
        const next = Math.max(0.15, Math.min(3, (zoomRef.current || 1) + delta));
        setZoom(next);
      } else {
        e.preventDefault();
        setPan(prev => ({
          x: prev.x - e.deltaX,
          y: prev.y - e.deltaY,
        }));
      }
    };
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, []);

  // Keep a ref to zoom so the wheel handler always reads the latest value
  const zoomRef = useRef(zoom);
  useEffect(() => { zoomRef.current = zoom; }, [zoom]);

  // ==================================================================
  // API calls
  // ==================================================================

  const fetchLineage = useCallback(async () => {
    if (!tableName.trim()) { setError('请输入表名'); return; }
    setLoading(true);
    setError(null);
    setGraphData(null);
    closeColumnPanel();
    try {
      const params = new URLSearchParams({ max_depth: String(depth), direction });
      if (connectionId.trim()) params.set('connection_id', connectionId.trim());
      if (database.trim())     params.set('database', database.trim());

      const res = await fetch(`/api/v1/lineage/table/${encodeURIComponent(tableName.trim())}?${params}`);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setGraphData(data);
      setZoom(1);
      setPan({ x: 0, y: 0 });
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  }, [tableName, connectionId, database, depth, direction]);

  const fetchColumnLineage = useCallback(async (nodeId, tblName) => {
    setColumnLoading(true);
    setColumnError(null);
    setColumnData(null);
    try {
      const params = new URLSearchParams();
      if (connectionId.trim()) params.set('connection_id', connectionId.trim());
      if (database.trim())     params.set('database', database.trim());

      const res = await fetch(
        `/api/v1/lineage/column/${encodeURIComponent(nodeId)}/${encodeURIComponent(tblName)}?${params}`
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      setColumnData(await res.json());
    } catch (err) {
      setColumnError(err.message);
    }
    setColumnLoading(false);
  }, [connectionId, database]);

  const analyzeSql = useCallback(async () => {
    if (!sqlInput.trim()) { setAnalyzeError('请输入 SQL'); return; }
    setAnalyzeLoading(true);
    setAnalyzeError(null);
    setAnalyzeResult(null);
    try {
      const r = await api.analyzeLineage({ sql: sqlInput, dialect: sqlDialect });
      setAnalyzeResult(r);
    } catch (err) {
      setAnalyzeError(err.message);
    }
    setAnalyzeLoading(false);
  }, [sqlInput, sqlDialect]);

  // ==================================================================
  // Interaction handlers
  // ==================================================================

  const handleMouseDown = useCallback((e) => {
    // Only left-button on the SVG background starts a pan
    if (e.button !== 0) return;
    setIsPanning(true);
    panStartRef.current  = { x: e.clientX, y: e.clientY };
    panOriginRef.current = { ...pan };
  }, [pan]);

  // We use window-level move/up for smooth panning even outside the SVG
  useEffect(() => {
    if (!isPanning) return;

    const onMove = (e) => {
      if (!panStartRef.current) return;
      setPan({
        x: panOriginRef.current.x + (e.clientX - panStartRef.current.x),
        y: panOriginRef.current.y + (e.clientY - panStartRef.current.y),
      });
    };
    const onUp = () => {
      setIsPanning(false);
      panStartRef.current = null;
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [isPanning]);

  const handleZoomIn  = useCallback(() => setZoom(z => Math.min(3, z + 0.15)), []);
  const handleZoomOut = useCallback(() => setZoom(z => Math.max(0.15, z - 0.15)), []);
  const handleFitView = useCallback(() => { setZoom(1); setPan({ x: 0, y: 0 }); }, []);

  const handleNodeClick = useCallback((node) => {
    setSelectedNodeId(prev => prev === node.id ? null : node.id);
    closeColumnPanel();
  }, []);

  const handleNodeDrillDown = useCallback((node) => {
    setSelectedNodeId(node.id);
    fetchColumnLineage(node.id, node.table_name);
  }, [fetchColumnLineage]);

  const closeColumnPanel = useCallback(() => {
    setColumnData(null);
    setColumnError(null);
    setColumnLoading(false);
  }, []);

  const handleReset = useCallback(() => {
    setGraphData(null);
    setLayout(null);
    setError(null);
    setSelectedNodeId(null);
    closeColumnPanel();
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [closeColumnPanel]);

  // ==================================================================
  // Derived values
  // ==================================================================

  const zoomPct   = Math.round(zoom * 100);
  const nodeCount = graphData?.nodes?.length ?? 0;
  const edgeCount = graphData?.edges?.length ?? 0;

  const getLayerStyle = useCallback((node) => {
    const key = (node.layer || '').toLowerCase();
    return LAYER_COLORS[key] || { ...DEFAULT_NODE_STYLE, label: (node.layer || '').toUpperCase() };
  }, []);

  // ==================================================================
  // Render
  // ==================================================================

  return (
    <div>
      {/* ---- Page header ---- */}
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#0f172a', margin: 0 }}>数据血缘</h1>
        <p style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
          可视化数据表血缘关系，追踪上下游依赖链路，分析 SQL 数据流向
        </p>
      </div>

      {/* ---- Tabs ---- */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, background: '#f1f5f9', borderRadius: 10, padding: 4, width: 'fit-content' }}>
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => { setActiveTab(key); setError(null); setAnalyzeError(null); }}
            style={{
              display: 'flex', alignItems: 'center', gap: 7,
              padding: '9px 18px', borderRadius: 8, border: 'none',
              fontSize: 13, fontWeight: activeTab === key ? 600 : 400,
              background: activeTab === key ? '#fff' : 'transparent',
              color: activeTab === key ? '#1e293b' : '#64748b',
              cursor: 'pointer',
              boxShadow: activeTab === key ? '0 1px 3px rgba(0,0,0,0.08)' : 'none',
              transition: 'all 0.15s',
            }}
          >
            <Icon style={{ width: 15, height: 15 }} /> {label}
          </button>
        ))}
      </div>

      {/* ================================================================
          TAB 1 — Table Lineage Tracing
          ================================================================ */}
      {activeTab === 'lineage' && (
        <>
          {/* Controls card */}
          <div style={S.card}>
            <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr 1fr 0.9fr 1fr auto', gap: 14, alignItems: 'end' }}>
              <Field label="表名 *">
                <input
                  value={tableName} onChange={e => setTableName(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && fetchLineage()}
                  placeholder="例：dws_order_summary"
                  style={S.input}
                />
              </Field>
              <Field label="Connection ID">
                <input value={connectionId} onChange={e => setConnectionId(e.target.value)} placeholder="可选" style={S.input} />
              </Field>
              <Field label="数据库">
                <input value={database} onChange={e => setDatabase(e.target.value)} placeholder="可选" style={S.input} />
              </Field>
              <Field label={`深度: ${depth}`}>
                <input
                  type="range" min={1} max={10} value={depth}
                  onChange={e => setDepth(Number(e.target.value))}
                  style={{ width: '100%', accentColor: '#6366f1' }}
                />
              </Field>
              <Field label="方向">
                <select value={direction} onChange={e => setDirection(e.target.value)} style={S.input}>
                  {DIRECTIONS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
                </select>
              </Field>
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={fetchLineage} disabled={loading} style={S.primaryBtn}>
                  {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                  追踪
                </button>
                {graphData && (
                  <button onClick={handleReset} style={S.ghostBtn} title="清除">
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Error */}
          {error && <ErrorBox message={error} onRetry={fetchLineage} />}

          {/* Loading */}
          {loading && <Spinner />}

          {/* Graph area */}
          {graphData && layout && (
            <div style={S.graphOuter}>
              {/* Toolbar */}
              <div style={S.graphToolbar}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <Table2 style={{ width: 15, height: 15, color: '#818cf8' }} />
                  <span style={{ fontSize: 12, color: '#cbd5e1', fontWeight: 600 }}>
                    {nodeCount} 个节点 &middot; {edgeCount} 条边
                    {graphData.root && (
                      <span style={{ color: '#818cf8', marginLeft: 8 }}>
                        Root: {graphData.root}
                      </span>
                    )}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <LayerLegend />
                  <div style={{ width: 1, height: 18, background: '#334155', margin: '0 8px' }} />
                  <button onClick={handleZoomOut} style={S.iconBtn} title="缩小"><ZoomOut style={{ width: 14, height: 14 }} /></button>
                  <span style={{ fontSize: 11, color: '#94a3b8', minWidth: 36, textAlign: 'center' }}>{zoomPct}%</span>
                  <button onClick={handleZoomIn} style={S.iconBtn} title="放大"><ZoomIn style={{ width: 14, height: 14 }} /></button>
                  <button onClick={handleFitView} style={S.iconBtn} title="适应画布"><Maximize2 style={{ width: 14, height: 14 }} /></button>
                </div>
              </div>

              {/* SVG canvas */}
              <div
                ref={svgContainerRef}
                style={S.svgContainer}
              >
                <svg
                  ref={svgRef}
                  width="100%" height="100%"
                  style={{ display: 'block', cursor: isPanning ? 'grabbing' : 'grab' }}
                  onMouseDown={handleMouseDown}
                >
                  <SvgDefs />

                  {/* Grid background pattern */}
                  <defs>
                    <pattern id="lg-grid" width="40" height="40" patternUnits="userSpaceOnUse">
                      <circle cx="1" cy="1" r="0.5" fill="#1e293b" />
                    </pattern>
                  </defs>
                  <rect width="100%" height="100%" fill="url(#lg-grid)" />

                  <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
                    {/* ---- Edges ---- */}
                    {(graphData.edges || []).map((edge, idx) => {
                      const sp = layout.positions[edge.source_id];
                      const tp = layout.positions[edge.target_id];
                      if (!sp || !tp) return null;
                      const d = edgePath(sp.x, sp.y, tp.x, tp.y);
                      const isHl = hoveredEdge === idx;
                      return (
                        <g key={`e-${idx}`}>
                          {/* Invisible fat hit-area */}
                          <path d={d} fill="none" stroke="transparent" strokeWidth={14}
                            style={{ cursor: 'pointer' }}
                            onMouseEnter={() => setHoveredEdge(idx)}
                            onMouseLeave={() => setHoveredEdge(null)}
                          />
                          <path
                            d={d} fill="none"
                            stroke={isHl ? '#818cf8' : '#475569'}
                            strokeWidth={isHl ? 2.2 : 1.5}
                            strokeDasharray={edge.edge_type === 'derived_from' ? '6,4' : undefined}
                            markerEnd={isHl ? 'url(#lg-arrow-hl)' : 'url(#lg-arrow)'}
                            style={{ pointerEvents: 'none', transition: 'stroke 0.15s, stroke-width 0.15s' }}
                          />
                          {/* Edge tooltip */}
                          {isHl && (
                            <title>
                              {`${edge.source_id} → ${edge.target_id}`}{edge.transformation ? `\n${edge.transformation}` : ''}
                              {edge.sql_snippet ? `\n\n${edge.sql_snippet}` : ''}
                            </title>
                          )}
                        </g>
                      );
                    })}

                    {/* ---- Nodes ---- */}
                    {(graphData.nodes || []).map(node => {
                      const pos = layout.positions[node.id];
                      if (!pos) return null;
                      const isRoot = node.id === graphData.root;
                      const isSel  = node.id === selectedNodeId;
                      return (
                        <DagNode
                          key={node.id}
                          node={node}
                          pos={pos}
                          isRoot={isRoot}
                          isSelected={isSel}
                          layerStyle={getLayerStyle(node)}
                          onMouseDown={e => e.stopPropagation()}
                          onClick={() => handleNodeClick(node)}
                        />
                      );
                    })}
                  </g>
                </svg>

                {/* Empty-graph message */}
                {graphData.nodes?.length === 0 && (
                  <div style={S.svgEmpty}>
                    <GitBranch style={{ width: 40, height: 40, opacity: 0.35 }} />
                    <p style={{ fontSize: 13 }}>未找到血缘数据</p>
                  </div>
                )}

                {/* ---- Column drill-down overlay ---- */}
                {selectedNodeId && (
                  <div style={S.columnPanel}>
                    <div style={S.columnPanelHeader}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Layers style={{ width: 14, height: 14, color: '#818cf8' }} />
                        <span style={{ fontWeight: 600, fontSize: 13 }}>列血缘详情</span>
                      </div>
                      <button onClick={() => { setSelectedNodeId(null); closeColumnPanel(); }} style={S.panelClose}>
                        <X style={{ width: 14, height: 14 }} />
                      </button>
                    </div>

                    <div style={{ padding: 14, fontSize: 12, color: '#cbd5e1' }}>
                      <div style={{ marginBottom: 10 }}>
                        <div style={{ fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em' }}>选中节点</div>
                        <div style={{ fontWeight: 600, marginTop: 2 }}>{selectedNodeId}</div>
                      </div>

                      <button
                        onClick={() => {
                          const n = graphData.nodes.find(n => n.id === selectedNodeId);
                          if (n) handleNodeDrillDown(n);
                        }}
                        disabled={columnLoading}
                        style={{ ...S.primaryBtn, width: '100%', justifyContent: 'center', marginBottom: 14 }}
                      >
                        {columnLoading
                          ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> 加载中…</>
                          : <><Search className="w-3.5 h-3.5" /> 查询列血缘</>}
                      </button>

                      {columnError && (
                        <div style={{ fontSize: 12, color: '#f87171', background: '#450a0a', borderRadius: 8, padding: 10, marginBottom: 10 }}>
                          {columnError}
                        </div>
                      )}

                      {columnData && (
                        <>
                          {columnData.upstream?.length > 0 && (
                            <div style={{ marginBottom: 14 }}>
                              <div style={S.sectionTitle}>
                                <ArrowRight style={{ width: 11, height: 11, color: '#fbbf24' }} /> 上游列
                              </div>
                              {columnData.upstream.map((u, i) => (
                                <div key={i} style={S.colEdgeCard}>
                                  <div style={S.colEdgePath}>{u.source_table}.{u.source_column}</div>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#64748b', fontSize: 10 }}>
                                    <ChevronRight style={{ width: 10, height: 10 }} /> {u.target_column}
                                  </div>
                                  {u.transformation && <div style={S.transformBadge}>{u.transformation}</div>}
                                </div>
                              ))}
                            </div>
                          )}

                          {columnData.downstream?.length > 0 && (
                            <div>
                              <div style={S.sectionTitle}>
                                <ArrowRight style={{ width: 11, height: 11, color: '#34d399' }} /> 下游列
                              </div>
                              {columnData.downstream.map((d, i) => (
                                <div key={i} style={S.colEdgeCard}>
                                  <div style={S.colEdgePath}>{d.target_table}.{d.target_column}</div>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#64748b', fontSize: 10 }}>
                                    <ChevronRight style={{ width: 10, height: 10 }} /> {d.source_column}
                                  </div>
                                  {d.transformation && <div style={S.transformBadge}>{d.transformation}</div>}
                                </div>
                              ))}
                            </div>
                          )}

                          {!columnData.upstream?.length && !columnData.downstream?.length && (
                            <div style={{ fontSize: 12, color: '#64748b', textAlign: 'center', padding: '12px 0' }}>
                              未找到列级别血缘关系
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* Hint bar */}
              <div style={S.hintBar}>
                点击节点查看列血缘 &middot; 滚轮平移画布 &middot; Ctrl + 滚轮缩放 &middot; 拖拽平移
              </div>
            </div>
          )}

          {/* Initial empty state */}
          {!graphData && !loading && !error && (
            <div style={S.emptyState}>
              <GitBranch style={{ width: 48, height: 48, color: '#94a3b8', opacity: 0.4, marginBottom: 12 }} />
              <p style={{ fontSize: 14, color: '#64748b', margin: 0 }}>
                输入表名并点击「追踪」以可视化数据血缘关系图
              </p>
              <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 6 }}>
                支持 ODS / DWD / DWS / ADS 分层自动着色
              </p>
            </div>
          )}
        </>
      )}

      {/* ================================================================
          TAB 2 — SQL Lineage Analysis
          ================================================================ */}
      {activeTab === 'analyze' && (
        <>
          <div style={S.card}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 14, alignItems: 'end' }}>
              <Field label="SQL 语句">
                <textarea
                  value={sqlInput}
                  onChange={e => setSqlInput(e.target.value)}
                  placeholder="粘贴 INSERT / CREATE TABLE AS SELECT 等 SQL 语句…&#10;&#10;示例：&#10;INSERT INTO dws_order_summary&#10;SELECT customer_id, SUM(amount)&#10;FROM ods_orders&#10;GROUP BY customer_id;"
                  style={{ ...S.input, fontFamily: '"JetBrains Mono", "Fira Code", "Cascadia Code", monospace', minHeight: 160, resize: 'vertical', lineHeight: 1.6 }}
                />
              </Field>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <Field label="方言">
                  <select value={sqlDialect} onChange={e => setSqlDialect(e.target.value)} style={{ ...S.input, minWidth: 130 }}>
                    {DIALECTS.map(d => <option key={d} value={d}>{d}</option>)}
                  </select>
                </Field>
                <button onClick={analyzeSql} disabled={analyzeLoading} style={{ ...S.primaryBtn, padding: '10px 22px' }}>
                  {analyzeLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                  分析
                </button>
              </div>
            </div>
          </div>

          {analyzeError && <ErrorBox message={analyzeError} onRetry={analyzeSql} />}
          {analyzeLoading && <Spinner />}

          {analyzeResult && (
            <div style={S.card}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18 }}>
                <div style={{ width: 32, height: 32, borderRadius: 8, background: '#eef2ff', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <GitBranch style={{ width: 16, height: 16, color: '#6366f1' }} />
                </div>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: '#0f172a' }}>SQL 血缘分析结果</div>
                  <div style={{ fontSize: 12, color: '#64748b' }}>
                    {(analyzeResult.source_tables || []).length} 个源表 &middot;
                    {' '}{(analyzeResult.target_tables || []).length} 个目标表 &middot;
                    {' '}{(analyzeResult.column_mappings || []).length} 个列映射
                  </div>
                </div>
              </div>

              {/* Source / target summary */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 18 }}>
                <div style={S.resultBox}>
                  <div style={S.resultBoxTitle}>源表 (Source)</div>
                  {(analyzeResult.source_tables || []).length > 0 ? (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {analyzeResult.source_tables.map((t, i) => (
                        <span key={i} style={S.tableChip}>{t}</span>
                      ))}
                    </div>
                  ) : <div style={S.noData}>无</div>}
                </div>
                <div style={S.resultBox}>
                  <div style={S.resultBoxTitle}>目标表 (Target)</div>
                  {(analyzeResult.target_tables || []).length > 0 ? (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {analyzeResult.target_tables.map((t, i) => (
                        <span key={i} style={{ ...S.tableChip, borderColor: '#22c55e', background: '#052e16', color: '#86efac' }}>{t}</span>
                      ))}
                    </div>
                  ) : <div style={S.noData}>无</div>}
                </div>
              </div>

              {/* Edges */}
              {(analyzeResult.edges || []).length > 0 && (
                <div style={{ marginBottom: 18 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 8 }}>数据流向</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {analyzeResult.edges.map((e, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#334155' }}>
                        <span style={S.tableChip}>{e.source}</span>
                        <ArrowRight style={{ width: 13, height: 13, color: '#94a3b8', flexShrink: 0 }} />
                        <span style={{ ...S.tableChip, borderColor: '#22c55e', background: '#052e16', color: '#86efac' }}>{e.target}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Column mappings table */}
              {(analyzeResult.column_mappings || []).length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 8 }}>列映射详情</div>
                  <div style={{ overflowX: 'auto', borderRadius: 10, border: '1px solid #e2e8f0' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                      <thead>
                        <tr style={{ background: '#f8fafc' }}>
                          {['源表', '源列', '目标表', '目标列', '转换'].map(h => (
                            <th key={h} style={S.th}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {analyzeResult.column_mappings.map((m, i) => (
                          <tr key={i} style={{ borderTop: '1px solid #f1f5f9' }}>
                            <td style={S.td}>{m.source_table}</td>
                            <td style={{ ...S.td, fontFamily: 'monospace', fontWeight: 500 }}>{m.source_column}</td>
                            <td style={S.td}>{m.target_table}</td>
                            <td style={{ ...S.td, fontFamily: 'monospace', fontWeight: 500 }}>{m.target_column}</td>
                            <td style={S.td}>
                              {m.transformation
                                ? <span style={S.transformBadgeLight}>{m.transformation}</span>
                                : <span style={{ color: '#cbd5e1' }}>—</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Initial empty state */}
          {!analyzeResult && !analyzeLoading && !analyzeError && (
            <div style={S.emptyState}>
              <Search style={{ width: 48, height: 48, color: '#94a3b8', opacity: 0.4, marginBottom: 12 }} />
              <p style={{ fontSize: 14, color: '#64748b', margin: 0 }}>
                粘贴 SQL 语句以自动解析数据血缘关系
              </p>
              <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 6 }}>
                支持 INSERT INTO ... SELECT / CREATE TABLE AS SELECT 等语法
              </p>
            </div>
          )}
        </>
      )}

      {/* ---- Component-scoped styles ---- */}
      <style>{`
        input:focus, select:focus, textarea:focus {
          outline: none;
          border-color: #818cf8 !important;
          box-shadow: 0 0 0 3px rgba(99,102,241,0.08);
        }
        input[type="range"] {
          height: 6px;
        }
        input[type="range"]::-webkit-slider-thumb {
          width: 16px;
          height: 16px;
        }
      `}</style>
    </div>
  );
}

// ======================================================================
// Style dictionary (inline CSS-in-JS objects)
// ======================================================================

const inputBase = {
  width: '100%',
  padding: '8px 12px',
  border: '1px solid #e2e8f0',
  borderRadius: 8,
  fontSize: 13,
  color: '#1e293b',
  background: '#fff',
  transition: 'border-color 0.15s, box-shadow 0.15s',
  boxSizing: 'border-box',
};

const S = {
  card: {
    background: '#fff',
    border: '1px solid #e2e8f0',
    borderRadius: 14,
    padding: '18px 20px',
    marginBottom: 16,
  },

  input: {
    ...inputBase,
  },

  primaryBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 7,
    padding: '8px 18px',
    background: '#6366f1',
    color: '#fff',
    fontSize: 13,
    fontWeight: 500,
    borderRadius: 9,
    border: 'none',
    cursor: 'pointer',
    transition: 'background 0.15s',
    whiteSpace: 'nowrap',
  },

  ghostBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '8px',
    background: 'transparent',
    color: '#94a3b8',
    fontSize: 13,
    borderRadius: 9,
    border: '1px solid #e2e8f0',
    cursor: 'pointer',
    transition: 'all 0.15s',
  },

  iconBtn: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 28,
    height: 28,
    borderRadius: 6,
    border: '1px solid #334155',
    background: 'transparent',
    color: '#94a3b8',
    cursor: 'pointer',
    transition: 'all 0.15s',
  },

  errorBox: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    background: '#fef2f2',
    border: '1px solid #fecaca',
    borderRadius: 14,
    padding: '12px 16px',
    marginBottom: 16,
    fontSize: 13,
    color: '#991b1b',
  },

  errorRetry: {
    marginLeft: 'auto',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 5,
    padding: '4px 12px',
    background: '#fee2e2',
    border: '1px solid #fecaca',
    borderRadius: 6,
    fontSize: 12,
    color: '#b91c1c',
    cursor: 'pointer',
    flexShrink: 0,
  },

  center: {
    display: 'flex',
    justifyContent: 'center',
    padding: '48px 0',
  },

  graphOuter: {
    borderRadius: 14,
    overflow: 'hidden',
    border: '1px solid #1e293b',
    marginBottom: 16,
  },

  graphToolbar: {
    background: '#0f172a',
    padding: '8px 14px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottom: '1px solid #1e293b',
  },

  svgContainer: {
    position: 'relative',
    background: '#0f172a',
    height: 520,
    overflow: 'hidden',
  },

  svgEmpty: {
    position: 'absolute',
    inset: 0,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#64748b',
    pointerEvents: 'none',
  },

  columnPanel: {
    position: 'absolute',
    top: 0,
    right: 0,
    width: 310,
    height: '100%',
    background: '#0f172a',
    borderLeft: '1px solid #1e293b',
    overflowY: 'auto',
    zIndex: 10,
  },

  columnPanelHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 14px',
    borderBottom: '1px solid #1e293b',
    color: '#e2e8f0',
  },

  panelClose: {
    background: 'transparent',
    border: 'none',
    color: '#64748b',
    cursor: 'pointer',
    padding: 4,
    display: 'flex',
    alignItems: 'center',
    borderRadius: 4,
    transition: 'color 0.15s',
  },

  sectionTitle: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 10,
    fontWeight: 700,
    color: '#64748b',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    marginBottom: 8,
  },

  colEdgeCard: {
    background: '#1e293b',
    borderRadius: 8,
    padding: '9px 12px',
    marginBottom: 6,
    fontSize: 12,
  },

  colEdgePath: {
    fontFamily: '"JetBrains Mono", "Fira Code", monospace',
    color: '#93c5fd',
    fontWeight: 500,
    marginBottom: 3,
  },

  transformBadge: {
    display: 'inline-block',
    marginTop: 5,
    padding: '2px 8px',
    background: '#1e1b4b',
    border: '1px solid #3730a3',
    borderRadius: 5,
    fontSize: 10,
    color: '#a5b4fc',
    fontFamily: 'monospace',
  },

  transformBadgeLight: {
    display: 'inline-block',
    padding: '2px 8px',
    background: '#eef2ff',
    border: '1px solid #c7d2fe',
    borderRadius: 5,
    fontSize: 11,
    color: '#4f46e5',
    fontFamily: 'monospace',
  },

  hintBar: {
    background: '#0f172a',
    padding: '8px 14px',
    fontSize: 11,
    color: '#475569',
    borderTop: '1px solid #1e293b',
    textAlign: 'center',
  },

  emptyState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '60px 20px',
    textAlign: 'center',
  },

  resultBox: {
    background: '#f8fafc',
    border: '1px solid #e2e8f0',
    borderRadius: 10,
    padding: '12px 14px',
  },

  resultBoxTitle: {
    fontSize: 11,
    fontWeight: 600,
    color: '#64748b',
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
    marginBottom: 8,
  },

  tableChip: {
    display: 'inline-block',
    padding: '3px 10px',
    background: '#172554',
    border: '1px solid #3b82f6',
    borderRadius: 6,
    fontSize: 12,
    color: '#93c5fd',
    fontFamily: 'monospace',
  },

  noData: {
    fontSize: 12,
    color: '#cbd5e1',
  },

  th: {
    textAlign: 'left',
    padding: '9px 12px',
    fontSize: 11,
    fontWeight: 600,
    color: '#64748b',
    textTransform: 'uppercase',
    letterSpacing: '0.03em',
  },

  td: {
    padding: '8px 12px',
    fontSize: 12,
    color: '#334155',
  },
};
