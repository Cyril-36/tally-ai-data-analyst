import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { ExpiredOverlay } from './components/ExpiredOverlay';
import { Spinner } from './components/Spinner';
import { AnomaliesScreen } from './screens/AnomaliesScreen';
import { AskScreen } from './screens/AskScreen';
import { AuthScreen } from './screens/AuthScreen';
import { DashboardScreen } from './screens/DashboardScreen';
import { DataScreen } from './screens/DataScreen';
import { QualityScreen } from './screens/QualityScreen';
import { SettingsScreen } from './screens/SettingsScreen';
import { AuthProvider, useAuth } from './state/auth';
import { ModalProvider } from './state/modal';
import { ThreadsProvider } from './state/threads';
import { ToastProvider } from './state/toast';
import { UploadsProvider } from './state/uploads';
import { WorkspaceProvider } from './state/workspace';

/** Admin pages exist only when the API's own `GET /api/auth/me` says role === "admin". */
function RequireAdmin({ children }: { children: JSX.Element }) {
  const { isAdmin } = useAuth();
  return isAdmin ? children : <Navigate to="/ask" replace />;
}

function Workspace() {
  const { expired } = useAuth();
  return (
    <>
      {/* Kept mounted but hidden while the session is expired, so the typed question survives. */}
      <div hidden={expired} aria-hidden={expired}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Navigate to="/ask" replace />} />
            <Route path="/ask" element={<AskScreen />} />
            <Route path="/ask/:conversationId" element={<AskScreen />} />
            <Route path="/data" element={<DataScreen />} />
            <Route path="/data/:datasetId" element={<DataScreen />} />
            <Route path="/quality" element={<QualityScreen />} />
            <Route path="/anomalies" element={<AnomaliesScreen />} />
            <Route path="/dashboard" element={<DashboardScreen />} />
            <Route
              path="/settings"
              element={
                <RequireAdmin>
                  <SettingsScreen />
                </RequireAdmin>
              }
            />
            <Route path="*" element={<Navigate to="/ask" replace />} />
          </Route>
        </Routes>
      </div>
      {expired && <ExpiredOverlay />}
    </>
  );
}

function Gate() {
  const { status, user } = useAuth();
  if (status === 'loading') {
    return (
      <div className="boot" role="status">
        <Spinner /> Loading…
      </div>
    );
  }
  if (status === 'out' || !user) return <AuthScreen />;
  return (
    <WorkspaceProvider key={user.id}>
      <ThreadsProvider>
        <UploadsProvider>
          <Workspace />
        </UploadsProvider>
      </ThreadsProvider>
    </WorkspaceProvider>
  );
}

/** Everything except the router, so tests can mount it in a MemoryRouter. */
export function App() {
  return (
    <ToastProvider>
      <ModalProvider>
        <AuthProvider>
          <Gate />
        </AuthProvider>
      </ModalProvider>
    </ToastProvider>
  );
}
