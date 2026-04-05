"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { UserMe } from "@/lib/api";
import { getMe, refreshToken as apiRefresh } from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface AuthState {
  token: string | null;
  user: UserMe | null;
  ready: boolean;
}

interface AuthContextValue extends AuthState {
  setToken: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/* ------------------------------------------------------------------ */
/*  Provider                                                           */
/* ------------------------------------------------------------------ */

const TOKEN_KEY = "sb_token";
const JWT_REFRESH_INTERVAL_MS = 10 * 60 * 1000;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    token: null,
    user: null,
    ready: false,
  });

  /* Hydrate from localStorage on mount */
  useEffect(() => {
    const stored = localStorage.getItem(TOKEN_KEY);
    if (!stored) {
      setState((s) => ({ ...s, ready: true }));
      return;
    }
    getMe(stored)
      .then((user) => setState({ token: stored, user, ready: true }))
      .catch(() => {
        localStorage.removeItem(TOKEN_KEY);
        setState({ token: null, user: null, ready: true });
      });
  }, []);

  /* Periodically refresh the JWT (every 10 min) */
  useEffect(() => {
    if (!state.token) return;
    const id = setInterval(
      () => {
        apiRefresh(state.token!)
          .then((r) => {
            localStorage.setItem(TOKEN_KEY, r.access_token);
            setState((s) => ({ ...s, token: r.access_token }));
          })
          .catch(() => {
            /* token expired — force logout */
            localStorage.removeItem(TOKEN_KEY);
            setState({ token: null, user: null, ready: true });
          });
      },
      JWT_REFRESH_INTERVAL_MS,
    );
    return () => clearInterval(id);
  }, [state.token]);

  const setToken = useCallback((token: string) => {
    localStorage.setItem(TOKEN_KEY, token);
    getMe(token).then((user) => setState({ token, user, ready: true }));
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setState({ token: null, user: null, ready: true });
  }, []);

  const value = useMemo(
    () => ({ ...state, setToken, logout }),
    [state, setToken, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/* ------------------------------------------------------------------ */
/*  Hook                                                               */
/* ------------------------------------------------------------------ */

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
