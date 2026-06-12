import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Connections from './pages/Connections';
import SqlQuery from './pages/SqlQuery';
import DdlBuilder from './pages/DdlBuilder';
import Governance from './pages/Governance';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Connections />} />
          <Route path="sql" element={<SqlQuery />} />
          <Route path="ddl" element={<DdlBuilder />} />
          <Route path="governance" element={<Governance />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
