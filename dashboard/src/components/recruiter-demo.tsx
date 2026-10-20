"use client";

import { useEffect, useState } from "react";
import { CalendarClock, Database, FileSearch, LoaderCircle, Send, ShieldCheck } from "lucide-react";


type DemoMode = "knowledge" | "availability";

type DemoStatus = {
  api: "online";
  supabase: "online";
  knowledge: "ready" | "empty";
  active_dentists: number;
  active_services: number;
  ready_documents: number;
};

type DemoResult = {
  mode: DemoMode;
  status: string;
  answer: string;
  latency_ms: number;
  sources: Array<{ filename: string; page_number: number | null; similarity: number }>;
  slots: Array<{
    dentist_name: string;
    service_name: string | null;
    start_at: string;
    end_at: string;
    duration_minutes: number;
    calendar_verified: boolean;
  }>;
};

const EXAMPLES: Record<DemoMode, string> = {
  knowledge: "How much is teeth whitening?",
  availability: "Find the next available teeth cleaning appointment",
};

async function readError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { detail?: string };
    return payload.detail || "The live demo could not complete the request.";
  } catch {
    return "The live demo could not complete the request.";
  }
}

export function RecruiterDemo() {
  const [mode, setMode] = useState<DemoMode>("knowledge");
  const [query, setQuery] = useState(EXAMPLES.knowledge);
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [statusMessage, setStatusMessage] = useState("Checking the live stack…");
  const [result, setResult] = useState<DemoResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/demo/status", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return response.json() as Promise<DemoStatus>;
      })
      .then((payload) => {
        setStatus(payload);
        setStatusMessage("Live services connected");
      })
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        setStatusMessage(requestError instanceof Error ? requestError.message : "Live status unavailable");
      });
    return () => controller.abort();
  }, []);

  function chooseMode(nextMode: DemoMode) {
    setMode(nextMode);
    setQuery(EXAMPLES[nextMode]);
    setResult(null);
    setError(null);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const response = await fetch("/api/demo/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, query }),
      });
      if (!response.ok) throw new Error(await readError(response));
      setResult(await response.json() as DemoResult);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The live demo is unavailable.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="recruiter-console">
      <div className="console-topline">
        <div className="live-indicator" data-online={status ? "true" : "false"}>
          <span aria-hidden="true" />
          {statusMessage}
        </div>
        <span className="console-security"><ShieldCheck size={15} /> Read-only demo</span>
      </div>

      <div className="demo-metrics" aria-label="Live project metrics">
        <div><Database size={17} /><strong>{status?.active_services ?? "—"}</strong><span>services</span></div>
        <div><CalendarClock size={17} /><strong>{status?.active_dentists ?? "—"}</strong><span>doctors</span></div>
        <div><FileSearch size={17} /><strong>{status?.ready_documents ?? "—"}</strong><span>RAG sources</span></div>
      </div>

      <div className="demo-tabs" role="tablist" aria-label="Demo query type">
        <button type="button" role="tab" aria-selected={mode === "knowledge"} onClick={() => chooseMode("knowledge")}>Knowledge RAG</button>
        <button type="button" role="tab" aria-selected={mode === "availability"} onClick={() => chooseMode("availability")}>Live availability</button>
      </div>

      <form onSubmit={submit} className="demo-form">
        <label htmlFor="demo-query">Ask the running system</label>
        <div className="demo-query-row">
          <input
            id="demo-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            minLength={3}
            maxLength={300}
            required
          />
          <button type="submit" disabled={loading} aria-label="Run live query">
            {loading ? <LoaderCircle className="spin" size={19} /> : <Send size={19} />}
          </button>
        </div>
        <p>Queries are validated, clinic-scoped, rate-limited, and cannot create or change appointments.</p>
      </form>

      <div className="demo-output" aria-live="polite">
        {!result && !error ? <p className="demo-placeholder">A grounded answer or calendar-verified slot result will appear here.</p> : null}
        {error ? <p className="demo-error" role="alert">{error}</p> : null}
        {result ? (
          <div className="demo-result">
            <div className="result-meta"><span>{result.status.replaceAll("_", " ")}</span><span>{result.latency_ms} ms</span></div>
            <p>{result.answer}</p>
            {result.sources.length ? (
              <ul className="source-list" aria-label="Retrieved sources">
                {result.sources.map((source, index) => (
                  <li key={`${source.filename}-${source.page_number}-${index}`}>
                    {source.filename}{source.page_number ? ` · page ${source.page_number}` : ""} · {(source.similarity * 100).toFixed(0)}% match
                  </li>
                ))}
              </ul>
            ) : null}
            {result.slots.length ? (
              <ul className="slot-list" aria-label="Verified appointment slots">
                {result.slots.map((slot) => (
                  <li key={`${slot.dentist_name}-${slot.start_at}`}>
                    <strong>{new Intl.DateTimeFormat("en-AE", { dateStyle: "medium", timeStyle: "short" }).format(new Date(slot.start_at))}</strong>
                    <span>{slot.dentist_name}{slot.service_name ? ` · ${slot.service_name}` : ""} · {slot.duration_minutes} min</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
