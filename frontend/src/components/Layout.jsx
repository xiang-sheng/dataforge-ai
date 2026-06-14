import { NavLink, Outlet } from 'react-router-dom';
import { Database, MessageSquare, Table, Shield, Layers, GitBranch } from 'lucide-react';

const NAV = [
  { to: '/', icon: Database, label: '连接管理' },
  { to: '/sql', icon: MessageSquare, label: '智能问数' },
  { to: '/ddl', icon: Table, label: 'DDL 建模' },
  { to: '/governance', icon: Shield, label: '数据治理' },
  { to: '/lineage', icon: GitBranch, label: '数据血缘' },
];

export default function Layout() {
  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className="w-56 bg-gray-900 text-gray-300 flex flex-col">
        <div className="px-5 py-4 flex items-center gap-2.5 border-b border-gray-800">
          <Layers className="w-6 h-6 text-indigo-400" />
          <span className="text-base font-semibold text-white tracking-tight">DataForge AI</span>
        </div>
        <nav className="flex-1 py-3 space-y-0.5 px-2.5">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  isActive
                    ? 'bg-indigo-600 text-white font-medium'
                    : 'hover:bg-gray-800 hover:text-white'
                }`
              }
            >
              <Icon className="w-4.5 h-4.5" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 text-xs text-gray-500 border-t border-gray-800">
          v0.3.0
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="max-w-5xl mx-auto px-8 py-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
