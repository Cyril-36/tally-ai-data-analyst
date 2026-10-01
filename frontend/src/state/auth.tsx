import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { auth as authApi } from '../api/endpoints';
import { ApiError, setUnauthorizedHandler } from '../api/client';
import type { User } from '../api/types';

interface AuthState {
  status: 'loading' | 'out' | 'in';
  user: User | null;
  /** The server rejected our session while the workspace was open. */
  expired: boolean;
}

interface AuthApi extends AuthState {
  /** True only when the API's own `GET /api/auth/me` says role === "admin". */
  isAdmin: boolean;
  signIn: (email: string, password: string) => Promise<User>;
  signUp: (email: string, password: string) => Promise<User>;
  signOut: () => Promise<void>;
}

const Ctx = createContext<AuthApi | null>(null);

export function useAuth(): AuthApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useAuth outside AuthProvider');
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading', user: null, expired: false });

  useEffect(() => {
    let live = true;
    authApi
      .me()
      .then((user) => live && setState({ status: 'in', user, expired: false }))
      .catch(() => live && setState({ status: 'out', user: null, expired: false }));
    return () => {
      live = false;
    };
  }, []);

  // Any 401 from a non-auth call: with a workspace open, keep it mounted but hidden behind the
  // "Session expired" sign-in so the typed question survives; otherwise show the sign-in screen.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setState((s) => (s.status === 'in' ? { ...s, expired: true } : s));
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const user = await authApi.login(email, password);
    setState({ status: 'in', user, expired: false });
    return user;
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    const user = await authApi.signup(email, password);
    setState({ status: 'in', user, expired: false });
    return user;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await authApi.logout();
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
    }
    setState({ status: 'out', user: null, expired: false });
  }, []);

  const value = useMemo<AuthApi>(
    () => ({ ...state, isAdmin: state.user?.role === 'admin', signIn, signUp, signOut }),
    [state, signIn, signUp, signOut],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
