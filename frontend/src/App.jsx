import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import Layout from './components/Layout';
import ErrorBoundary from './components/ErrorBoundary';
import Connections from './pages/Connections';
import SqlQuery from './pages/SqlQuery';
import DdlBuilder from './pages/DdlBuilder';
import Governance from './pages/Governance';

function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="w-14 h-14 bg-gray-800 rounded-full flex items-center justify-center mb-4">
        <span className="text-2xl font-bold text-gray-400">404</span>
      </div>
      <h1 className="text-lg font-semibold text-gray-100 mb-2">Page not found</h1>
      <p className="text-sm text-gray-400 mb-6">
        The page you are looking for does not exist or has been moved.
      </p>
      <Link
        to="/"
        className="inline-flex items-center gap-2 px-5 py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition"
      >
        Back to home
      </Link>
    </div>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Connections />} />
            <Route path="sql" element={<SqlQuery />} />
            <Route path="ddl" element={<DdlBuilder />} />
            <Route path="governance" element={<Governance />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}

export default App;
