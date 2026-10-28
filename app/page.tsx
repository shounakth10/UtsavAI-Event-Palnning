"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useAuth } from "./auth-context";
import { labels } from "./app.js";
import type {
  ChatEntry,
  CriticReport,
  EventPlan,
  Language,
  PlanChange,
} from "./types";

type ApiResult = {
  status: string;
  plan?: EventPlan;
  critic_report?: CriticReport | null;
  fixed_issues?: number;
  questions?: string[];
  reply?: string;
  updated_plan?: EventPlan | null;
  changes?: PlanChange[];
  text?: string;
  sent?: boolean;
  created?: number;
  updated?: number;
};

const languageNames: Record<Language, string> = {
  en: "English",
  hi: "हिन्दी",
  mr: "मराठी",
};

function formatINR(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(amount);
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

async function apiRequest(
  path: string,
  body?: unknown,
  method = "POST",
  idToken?: string,
): Promise<{ response: Response; data: ApiResult }> {
  const apiBase =
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    (window.location.port === "3000"
      ? "http://localhost:8000"
      : window.location.origin);
  const response = await fetch(`${apiBase}${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(idToken ? { Authorization: `Bearer ${idToken}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = (await response.json().catch(() => ({}))) as ApiResult & {
    detail?: string;
    message?: string;
  };
  if (!response.ok) {
    throw new Error(data.detail ?? data.message ?? `Request failed (${response.status}).`);
  }
  return { response, data };
}

export default function Home() {
  const [language, setLanguage] = useState<Language>("en");
  const [description, setDescription] = useState("");
  const [clarificationQuestions, setClarificationQuestions] = useState<string[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<EventPlan | null>(null);
  const [criticReport, setCriticReport] = useState<CriticReport | null>(null);
  const [fixedIssues, setFixedIssues] = useState(0);
  const [chat, setChat] = useState<ChatEntry[]>([]);
  const [chatMessage, setChatMessage] = useState("");
  const [changes, setChanges] = useState<PlanChange[]>([]);
  const [busy, setBusy] = useState<"plan" | "demo" | "chat" | "export" | "calendar" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const text = useMemo(() => labels[language], [language]);
  const { user, loading: authLoading, configured, signIn, signOutUser, getCalendarAccessToken } = useAuth();

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  async function handleSignIn() {
    setError("");
    try {
      await signIn();
    } catch (authError) {
      setError(authError instanceof Error ? authError.message : text.errors);
    }
  }

  async function handleSignOut() {
    setError("");
    try {
      await signOutUser();
      setPlan(null);
      setChat([]);
      setChanges([]);
    } catch (authError) {
      setError(authError instanceof Error ? authError.message : text.errors);
    }
  }

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy("plan");
    try {
      if (!user) throw new Error("Sign in to create a private event plan.");
      const idToken = await user.getIdToken();
      const { data } = await apiRequest("/api/plan", {
        description,
        language,
        clarification_answers: Object.keys(answers).length ? answers : null,
      }, "POST", idToken);
      if (data.status === "needs_clarification") {
        setClarificationQuestions(data.questions ?? []);
        setAnswers({});
        setPlan(null);
        return;
      }
      if (!data.plan) throw new Error("The planner returned an incomplete response.");
      setPlan(data.plan);
      setCriticReport(data.critic_report ?? null);
      setFixedIssues(data.fixed_issues ?? 0);
      setClarificationQuestions([]);
      setChat([]);
      setChanges([]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : text.errors);
    } finally {
      setBusy(null);
    }
  }

  async function loadDemo() {
    setError("");
    setNotice("");
    setBusy("demo");
    try {
      const { data } = await apiRequest("/api/demo", undefined, "GET");
      if (!data.plan) throw new Error("The demo plan could not be loaded.");
      setPlan(data.plan);
      setCriticReport(data.critic_report ?? null);
      setFixedIssues(data.fixed_issues ?? 0);
      setDescription(data.plan.event.summary);
      setClarificationQuestions([]);
      setChat([]);
      setChanges([]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : text.errors);
    } finally {
      setBusy(null);
    }
  }

  async function sendChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plan || !chatMessage.trim()) return;
    const message = chatMessage.trim();
    setError("");
    setNotice("");
    setBusy("chat");
    try {
      if (!user) throw new Error("Sign in to use the event copilot.");
      const idToken = await user.getIdToken();
      const { data } = await apiRequest("/api/chat", {
        message,
        plan,
        language,
        history: chat,
      }, "POST", idToken);
      setChat((previous) => [
        ...previous,
        { role: "user", content: message },
        { role: "assistant", content: data.reply ?? "" },
      ]);
      if (data.updated_plan) setPlan(data.updated_plan);
      setCriticReport(data.critic_report ?? criticReport);
      setFixedIssues(data.fixed_issues ?? 0);
      setChanges(data.changes ?? []);
      setChatMessage("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : text.errors);
    } finally {
      setBusy(null);
    }
  }

  async function download(path: string) {
    if (!plan || !user) return;
    setError("");
    setNotice("");
    setBusy("export");
    try {
      const apiBase =
        process.env.NEXT_PUBLIC_API_BASE_URL ??
        (window.location.port === "3000"
          ? "http://localhost:8000"
          : window.location.origin);
      const response = await fetch(`${apiBase}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${await user.getIdToken()}`,
        },
        body: JSON.stringify(plan),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail ?? `Export failed (${response.status}).`);
      }
      const blob = await response.blob();
      const filename =
        response.headers.get("content-disposition")?.match(/filename="?([^"]+)"?/)?.[1] ??
        "utsavai-export";
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : text.errors);
    } finally {
      setBusy(null);
    }
  }

  async function copyWhatsApp() {
    if (!plan) return;
    setError("");
    setNotice("");
    setBusy("export");
    try {
      if (!user) throw new Error("Sign in to export your plan.");
      const { data } = await apiRequest(
        "/api/export/whatsapp",
        plan,
        "POST",
        await user.getIdToken(),
      );
      if (!data.text) throw new Error("The WhatsApp summary was empty.");
      await navigator.clipboard.writeText(data.text);
      setNotice(text.copied);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : text.errors);
    } finally {
      setBusy(null);
    }
  }

  async function emailPlan() {
    if (!plan || !user) return;
    setError("");
    setNotice("");
    setBusy("export");
    try {
      const { data } = await apiRequest(
        "/api/email/plan",
        plan,
        "POST",
        await user.getIdToken(),
      );
      if (!data.sent) throw new Error("The email was not accepted for delivery.");
      setNotice(text.emailDone);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : text.errors);
    } finally {
      setBusy(null);
    }
  }

  async function addToGoogleCalendar() {
    if (!plan || !user) return;
    setError("");
    setNotice("");
    setBusy("calendar");
    try {
      const [idToken, googleAccessToken] = await Promise.all([
        user.getIdToken(),
        getCalendarAccessToken(),
      ]);
      const { data } = await apiRequest(
        "/api/calendar/sync",
        { plan, google_access_token: googleAccessToken },
        "POST",
        idToken,
      );
      setNotice(`${text.calendarDone} · ${data.created ?? 0} created, ${data.updated ?? 0} updated`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : text.errors);
    } finally {
      setBusy(null);
    }
  }

  function toggleTask(taskId: string) {
    setPlan((current) =>
      current
        ? {
            ...current,
            timeline: current.timeline.map((task) =>
              task.id === taskId
                ? { ...task, status: task.status === "done" ? "todo" : "done" }
                : task,
            ),
          }
        : current,
    );
  }

  if (!configured || authLoading || !user) {
    return (
      <main className="app-shell auth-shell">
        <header className="topbar">
          <a className="brand" href="#top" aria-label="UtsavAI home">
            <span className="brand-mark" aria-hidden="true">✦</span>
            <span>{text.brand}</span>
          </a>
          <label className="language-control">
            <span>{text.language}</span>
            <select
              value={language}
              onChange={(event) => setLanguage(event.target.value as Language)}
              aria-label={text.language}
            >
              {Object.entries(languageNames).map(([code, name]) => (
                <option key={code} value={code}>{name}</option>
              ))}
            </select>
          </label>
        </header>
        <section className="auth-card" id="top">
          <span className="auth-symbol" aria-hidden="true">✿</span>
          <p className="eyebrow">{text.intro}</p>
          <h1>{text.loginTitle}</h1>
          <p className="muted">
            {configured
              ? text.loginHint
              : text.firebaseSetup}
          </p>
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          {configured && !authLoading && (
            <button className="button button-primary auth-button" onClick={handleSignIn}>
              {text.signIn}
            </button>
          )}
        </section>
        <footer className="footer">{text.footer}</footer>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="UtsavAI home">
          <span className="brand-mark" aria-hidden="true">✦</span>
          <span>{text.brand}</span>
        </a>
        <div className="header-actions">
          <span className="signed-in-email">{text.signedInAs}: {user.email}</span>
          <button className="button button-secondary sign-out-button" onClick={handleSignOut}>
            {text.signOut}
          </button>
          <label className="language-control">
            <span>{text.language}</span>
            <select
              value={language}
              onChange={(event) => setLanguage(event.target.value as Language)}
              aria-label={text.language}
            >
              {Object.entries(languageNames).map(([code, name]) => (
                <option key={code} value={code}>{name}</option>
              ))}
            </select>
          </label>
        </div>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <p className="eyebrow">{text.intro}</p>
          <h1>{text.tagline}</h1>
          <p className="hero-description">
            {language === "en"
              ? "Bring your people together with a practical plan for every detail, from your first booking to the final celebration."
              : language === "hi"
                ? "पहली बुकिंग से उत्सव के दिन तक, हर छोटी-बड़ी तैयारी के लिए एक व्यावहारिक योजना बनाएं।"
                : "पहिल्या बुकिंगपासून समारंभाच्या दिवसापर्यंत प्रत्येक तयारीसाठी सुटसुटीत आराखडा तयार करा."}
          </p>
        </div>
        <div className="hero-ornament" aria-hidden="true">
          <span>✿</span><span>✧</span><span>❋</span>
        </div>
      </section>

      <section className="planner-grid" aria-label={text.intro}>
        <div className="panel intake-panel">
          <div className="panel-heading">
            <div>
              <span className="step-number">01</span>
              <h2>{text.description}</h2>
            </div>
            <span className="panel-icon" aria-hidden="true">✧</span>
          </div>
          <p className="muted">{text.descriptionHint}</p>
          <form onSubmit={createPlan} className="planner-form">
            <label className="sr-only" htmlFor="event-description">{text.description}</label>
            <textarea
              id="event-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={1500}
              required
              rows={5}
              placeholder={
                language === "en"
                  ? "A Maharashtrian wedding in Pune on 20 January for 450 guests, with a ₹25 lakh budget…"
                  : language === "hi"
                    ? "पुणे में 20 जनवरी को 450 मेहमानों के लिए मराठी शादी, बजट ₹25 लाख…"
                    : "पुण्यात 20 जानेवारीला 450 पाहुण्यांसाठी मराठी लग्न, बजेट ₹25 लाख…"
              }
            />
            <div className="form-meta">
              <span>{description.length}/1500</span>
              <span>{text.estimated}</span>
            </div>
            <div className="form-actions">
              <button className="button button-primary" type="submit" disabled={busy !== null}>
                {busy === "plan" ? text.loading : text.generate}
              </button>
              <button className="button button-secondary" type="button" onClick={loadDemo} disabled={busy !== null}>
                {busy === "demo" ? text.loadingDemo : text.demo}
              </button>
            </div>
          </form>

          {clarificationQuestions.length > 0 && (
            <form className="clarification-box" onSubmit={createPlan}>
              <h3>{text.questions}</h3>
              {clarificationQuestions.map((question) => (
                <label key={question} className="field-label">
                  <span>{question}</span>
                  <input
                    value={answers[question] ?? ""}
                    onChange={(event) =>
                      setAnswers((current) => ({ ...current, [question]: event.target.value }))
                    }
                    required
                  />
                </label>
              ))}
              <button className="button button-primary" type="submit" disabled={busy !== null}>
                {text.continue}
              </button>
            </form>
          )}
        </div>

        <div className="panel plan-panel">
          <div className="panel-heading">
            <div>
              <span className="step-number">02</span>
              <h2>{text.eventDetails}</h2>
            </div>
            {criticReport && (
              <span className={`verification-badge ${criticReport.passed ? "is-verified" : "needs-review"}`}>
                {criticReport.passed
                  ? fixedIssues > 0
                    ? text.fixed.replace("{count}", String(fixedIssues))
                    : text.verified
                  : text.needsReview}
              </span>
            )}
          </div>
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          {notice && <div className="alert alert-success" role="status">{notice}</div>}
          {busy && <div className="sr-only" role="status">{busy === "plan" ? text.loading : busy === "demo" ? text.loadingDemo : busy === "chat" ? text.sending : busy === "calendar" ? text.calendarWorking : text.exportPdf}</div>}
          {!plan && (
            <div className="empty-state">
              <span className="empty-icon" aria-hidden="true">✿</span>
              <p>{busy === "plan" ? text.loading : text.noPlan}</p>
            </div>
          )}
          {plan && (
            <div className="plan-content">
              <section className="event-summary">
                <h3>{plan.event.title}</h3>
                <p>{plan.event.summary}</p>
                <div className="event-facts">
                  <span>📅 {plan.event.date}</span>
                  <span>📍 {plan.event.city}</span>
                  <span>👥 {plan.event.guest_count} {text.guestCount.toLowerCase()}</span>
                  <span>💰 {formatINR(plan.event.total_budget_inr)}</span>
                </div>
                <div className="event-tags">
                  <span>{plan.event.type}</span>
                  <span>{plan.event.cultural_style}</span>
                  <span>{plan.event.theme}</span>
                </div>
              </section>

              {criticReport && !criticReport.passed && (
                <details className="critic-issues">
                  <summary>{text.needsReview} · {criticReport.issues.length}</summary>
                  <ul>
                    {criticReport.issues.map((issue, index) => (
                      <li key={`${issue.message}-${index}`}>
                        <strong>{issue.severity.toUpperCase()}</strong> {issue.message}
                        <small>{issue.fix_suggestion}</small>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              <div className="plan-details-grid">
                <div className="plan-main-content">
                  <section className="content-section">
                    <h3>{text.budget}</h3>
                    <div className="budget-list">
                      {plan.budget.map((item) => (
                        <article className="budget-item" key={item.category}>
                          <div className="budget-item-top">
                            <strong>{item.category}</strong>
                            <span>{formatINR(item.amount_inr)}</span>
                          </div>
                          <div className="budget-bar" aria-label={`${item.percentage}%`}>
                            <span style={{ width: `${Math.min(100, item.percentage)}%` }} />
                          </div>
                          <div className="budget-item-bottom">
                            <span>{item.percentage.toFixed(1)}% · {item.priority}</span>
                            <p>{item.rationale}</p>
                          </div>
                        </article>
                      ))}
                    </div>
                  </section>

                  <section className="content-section">
                    <h3>{text.vendors}</h3>
                    <div className="vendor-list">
                      {plan.vendors.map((vendor) => (
                        <details className="vendor-card" key={vendor.category}>
                          <summary>
                            <span><strong>{vendor.category}</strong><small>{vendor.quantity_or_scope}</small></span>
                            <span>{formatINR(vendor.estimated_cost_inr)}</span>
                          </summary>
                          <p>{vendor.what_to_book}</p>
                          <p><strong>{text.due}:</strong> {vendor.book_by_date}</p>
                          <p><strong>{text.questionsToAsk}:</strong> {vendor.questions_to_ask.join(" · ")}</p>
                          <p><strong>{text.redFlags}:</strong> {vendor.red_flags.join(" · ")}</p>
                          <p><strong>{text.negotiation}:</strong> {vendor.negotiation_tip}</p>
                        </details>
                      ))}
                    </div>
                  </section>

                  <section className="content-section">
                    <h3>{text.ideas}</h3>
                    <div className="idea-grid">
                      {plan.ideas.map((idea) => (
                        <article className="idea-card" key={`${idea.type}-${idea.title}`}>
                          <span className="idea-kicker">{idea.type} · {idea.tier}</span>
                          <h4>{idea.title}</h4>
                          <p>{idea.description}</p>
                          <small>{formatINR(idea.estimated_cost_inr)}</small>
                        </article>
                      ))}
                    </div>
                  </section>

                  <div className="advisory-grid">
                    <section className="content-section advisory-card">
                      <h3>{text.risks}</h3>
                      <ul>{plan.risks.map((risk, index) => <li key={`${risk}-${index}`}>{risk}</li>)}</ul>
                    </section>
                    <section className="content-section advisory-card">
                      <h3>{text.assumptions}</h3>
                      <ul>{plan.assumptions.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
                    </section>
                  </div>

                  <section className="content-section export-section">
                    <h3>{text.share}</h3>
                    <p className="muted">{text.connectServicesHint}</p>
                    <div className="export-actions">
                      <button className="button button-secondary" onClick={() => download("/api/export/pdf")} disabled={busy !== null}>
                        {text.exportPdf}
                      </button>
                      <button className="button button-secondary" onClick={copyWhatsApp} disabled={busy !== null}>
                        {text.copyWhatsapp}
                      </button>
                      <button className="button button-secondary" onClick={emailPlan} disabled={busy !== null}>
                        {busy === "export" ? text.emailWorking : text.emailPlan}
                      </button>
                      <button className="button button-secondary" onClick={addToGoogleCalendar} disabled={busy !== null}>
                        {busy === "calendar" ? text.calendarWorking : text.calendar}
                      </button>
                    </div>
                  </section>

                  <section className="content-section copilot-section">
                    <h3>{text.chat}</h3>
                    <div className="chat-log" role="log" aria-live="polite" aria-label={text.chat}>
                      {chat.map((entry, index) => (
                        <p className={`chat-message ${entry.role}`} key={`${entry.role}-${index}`}>
                          <span>{entry.role === "user" ? text.you : text.brand}</span>
                          {entry.content}
                        </p>
                      ))}
                      {chat.length === 0 && <p className="muted">{text.chatHint}</p>}
                    </div>
                    <form className="chat-form" onSubmit={sendChat} aria-busy={busy === "chat"}>
                      <label className="sr-only" htmlFor="chat-message">{text.chatHint}</label>
                      <input
                        id="chat-message"
                        value={chatMessage}
                        onChange={(event) => setChatMessage(event.target.value)}
                        placeholder={text.chatHint}
                        maxLength={1500}
                        required
                      />
                      <button className="button button-primary" type="submit" disabled={busy !== null}>
                        {busy === "chat" ? text.sending : text.send}
                      </button>
                    </form>
                    {changes.length > 0 && (
                      <section className="changes-card" aria-live="polite">
                        <h4>{text.whatChanged}</h4>
                        <ul>
                          {changes.map((change, index) => (
                            <li key={`${change.field}-${index}`}>
                              <strong>{change.field}</strong>
                              <span>{displayValue(change.before)} → {displayValue(change.after)}</span>
                            </li>
                          ))}
                        </ul>
                      </section>
                    )}
                  </section>
                </div>

                <aside className="content-section timeline-sidebar" aria-label={text.timeline}>
                  <h3>{text.timeline}</h3>
                  <ol className="timeline-list">
                    {plan.timeline.map((task) => (
                      <li key={task.id} className={task.status === "done" ? "task-done" : ""}>
                        <label className="task-row">
                          <input
                            type="checkbox"
                            checked={task.status === "done"}
                            onChange={() => toggleTask(task.id)}
                          />
                          <span className="task-content">
                            <strong>{task.task}</strong>
                            <span>{task.due_date} · {task.category} · {text.owner}: {task.owner}</span>
                            {task.depends_on.length > 0 && (
                              <span>{text.dependencies}: {task.depends_on.join(", ")}</span>
                            )}
                          </span>
                        </label>
                      </li>
                    ))}
                  </ol>
                </aside>
              </div>
            </div>
          )}
        </div>
      </section>
      <footer className="footer">{text.footer}</footer>
    </main>
  );
}
