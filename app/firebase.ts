import { getApp, getApps, initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

const apiKey = process.env.NEXT_PUBLIC_FIREBASE_API_KEY;
const authDomain = process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN;
const projectId = process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
const appId = process.env.NEXT_PUBLIC_FIREBASE_APP_ID;

export const firebaseConfigured = Boolean(apiKey && authDomain && projectId && appId);

const app = firebaseConfigured
  ? getApps().length
    ? getApp()
    : initializeApp({
        apiKey,
        authDomain,
        projectId,
        appId,
      })
  : null;

export const firebaseAuth = app ? getAuth(app) : null;
