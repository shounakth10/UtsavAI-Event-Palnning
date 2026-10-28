"use client";

import {
  GoogleAuthProvider,
  onAuthStateChanged,
  reauthenticateWithPopup,
  signInWithPopup,
  signOut,
} from "firebase/auth";
import type { User } from "firebase/auth";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";

import { firebaseAuth, firebaseConfigured } from "./firebase";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  configured: boolean;
  signIn: () => Promise<void>;
  signOutUser: () => Promise<void>;
  getCalendarAccessToken: () => Promise<string>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!firebaseAuth) {
      setLoading(false);
      return;
    }
    return onAuthStateChanged(firebaseAuth, (currentUser) => {
      setUser(currentUser);
      setLoading(false);
    });
  }, []);

  const signIn = useCallback(async () => {
    if (!firebaseAuth) throw new Error("Firebase is not configured for this website.");
    await signInWithPopup(firebaseAuth, new GoogleAuthProvider());
  }, []);

  const signOutUser = useCallback(async () => {
    if (!firebaseAuth) throw new Error("Firebase is not configured for this website.");
    await signOut(firebaseAuth);
  }, []);

  const getCalendarAccessToken = useCallback(async () => {
    if (!firebaseAuth || !user) throw new Error("Sign in before connecting Google Calendar.");
    if (!user.emailVerified || !user.email) {
      throw new Error("Verify your Google account email before connecting Calendar.");
    }
    const provider = new GoogleAuthProvider();
    provider.addScope("https://www.googleapis.com/auth/calendar.events");
    provider.addScope("email");
    provider.setCustomParameters({ prompt: "consent select_account" });
    const result = await reauthenticateWithPopup(user, provider);
    const credential = GoogleAuthProvider.credentialFromResult(result);
    if (result.user.uid !== user.uid || result.user.email?.toLowerCase() !== user.email.toLowerCase()) {
      throw new Error("Connect Calendar using the same Google account you signed in with.");
    }
    if (!credential?.accessToken) {
      throw new Error("Google did not return Calendar access. Please try connecting again.");
    }
    return credential.accessToken;
  }, [user]);

  const value = useMemo(
    () => ({
      user,
      loading,
      configured: firebaseConfigured,
      signIn,
      signOutUser,
      getCalendarAccessToken,
    }),
    [user, loading, signIn, signOutUser, getCalendarAccessToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider.");
  return context;
}
