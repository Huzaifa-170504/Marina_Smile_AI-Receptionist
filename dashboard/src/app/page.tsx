import Link from "next/link";
import { ArrowRight, Bot, Braces, CalendarCheck, CheckCircle2, Database, GitBranch, LockKeyhole, MessagesSquare, ShieldCheck, Stethoscope } from "lucide-react";

import { RecruiterDemo } from "@/components/recruiter-demo";


const GITHUB_URL = "https://github.com/Huzaifa-170504/Marina_Smile_AI-Receptionist";

export default function Home() {
  return (
    <main className="recruiter-site">
      <nav className="recruiter-nav" aria-label="Primary navigation">
        <a className="recruiter-brand" href="#top"><span>MS</span><strong>Marina Smile AI</strong></a>
        <div className="recruiter-nav-links">
          <a href="#architecture">Architecture</a>
          <a href="#live-demo">Live demo</a>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub</a>
          <Link className="nav-cta" href="/login">Staff portal <ArrowRight size={15} /></Link>
        </div>
      </nav>

      <section className="recruiter-hero" id="top">
        <div className="hero-copy">
          <p className="recruiter-kicker"><span /> Production-minded AI clinic operations</p>
          <h1>From patient question to clinic action—without letting the model invent the truth.</h1>
          <p className="hero-lede">
            A tenant-aware dental receptionist platform combining grounded Gemini responses, deterministic scheduling tools, Supabase authorization, Google Calendar verification, and a role-aware operations dashboard.
          </p>
          <div className="hero-actions">
            <a className="recruiter-primary" href="#live-demo">Try the live system <ArrowRight size={17} /></a>
            <a className="recruiter-secondary" href={GITHUB_URL} target="_blank" rel="noreferrer"><GitBranch size={17} /> Explore the source</a>
          </div>
          <ul className="hero-proof" aria-label="System guarantees">
            <li><CheckCircle2 size={16} /> Database-backed authorization</li>
            <li><CheckCircle2 size={16} /> Calendar-verified availability</li>
            <li><CheckCircle2 size={16} /> Grounded RAG responses</li>
          </ul>
        </div>
        <div className="hero-system-card" aria-label="System request flow">
          <div className="system-card-header"><span>REQUEST TRACE</span><span className="trace-live">live architecture</span></div>
          <div className="trace-step"><MessagesSquare size={20} /><div><strong>Patient message</strong><small>English or Arabic intent</small></div><span>01</span></div>
          <div className="trace-line" />
          <div className="trace-step active"><Bot size={20} /><div><strong>Bounded AI orchestration</strong><small>Structured intent, no source-of-truth access</small></div><span>02</span></div>
          <div className="trace-line" />
          <div className="trace-split">
            <div><CalendarCheck size={19} /><strong>Calendar</strong><small>Availability</small></div>
            <div><Braces size={19} /><strong>Tools</strong><small>Rules</small></div>
            <div><LockKeyhole size={19} /><strong>Supabase</strong><small>State + RLS</small></div>
          </div>
          <div className="trace-result"><ShieldCheck size={19} /><span><strong>Verified response</strong><small>Only after trusted systems confirm</small></span></div>
        </div>
      </section>

      <section className="recruiter-strip" aria-label="Technology stack">
        <span>FastAPI</span><span>Next.js 16</span><span>Supabase</span><span>Gemini</span><span>Google Calendar</span><span>PostgreSQL</span>
      </section>

      <section className="recruiter-section architecture-section" id="architecture">
        <div className="section-heading">
          <p className="recruiter-kicker">System architecture</p>
          <h2>LLM reasoning at the edge. Deterministic controls at the core.</h2>
          <p>The model interprets language and produces grounded copy. Application services and PostgreSQL remain authoritative for identity, clinic records, availability, payment gates, and appointment state.</p>
        </div>
        <div className="architecture-grid">
          <article><span>01</span><MessagesSquare size={24} /><h3>Conversation boundary</h3><p>Short, natural patient interactions enter through adapters designed for idempotent webhook processing.</p></article>
          <article><span>02</span><Bot size={24} /><h3>Grounded intelligence</h3><p>Gemini uses structured output and retrieved clinic context. Prompt injection and unsupported medical guidance fall back safely.</p></article>
          <article><span>03</span><Braces size={24} /><h3>Application tools</h3><p>Typed Python services resolve clinics, doctors, services, schedules, conflicts, and lifecycle transitions independently of the prompt.</p></article>
          <article><span>04</span><LockKeyhole size={24} /><h3>Authoritative state</h3><p>Supabase RLS, tenant-scoped foreign keys, database constraints, and audit records protect operational data.</p></article>
        </div>
      </section>

      <section className="recruiter-section demo-section" id="live-demo">
        <div className="demo-copy">
          <p className="recruiter-kicker light">Live integration</p>
          <h2>Query the deployed project, not a scripted mock.</h2>
          <p>The console passes through a server-only Vercel proxy to FastAPI. Knowledge answers use live Supabase vectors and Gemini; availability checks combine clinic schedules, appointment conflicts, and Google Calendar.</p>
          <div className="demo-boundaries">
            <div><ShieldCheck size={20} /><span><strong>Read-only by design</strong><small>No public mutation tools</small></span></div>
            <div><LockKeyhole size={20} /><span><strong>Server-side secrets</strong><small>No privileged key in the browser</small></span></div>
            <div><Database size={20} /><span><strong>Persistent quota</strong><small>Database-backed abuse controls</small></span></div>
          </div>
        </div>
        <RecruiterDemo />
      </section>

      <section className="recruiter-section capability-section">
        <div className="section-heading compact">
          <p className="recruiter-kicker">Operational surface</p>
          <h2>One platform, distinct trust boundaries.</h2>
        </div>
        <div className="capability-grid">
          <article><Stethoscope size={25} /><h3>Patient experience</h3><p>Grounded clinic answers, availability discovery, booking workflow foundations, appointment changes, and human handoff.</p></article>
          <article><CalendarCheck size={25} /><h3>Clinic operations</h3><p>Role-aware appointments, queues, payments, doctors, schedules, staff permissions, handoffs, and audit visibility.</p></article>
          <article><ShieldCheck size={25} /><h3>Production controls</h3><p>Tenant propagation, RLS, idempotency, overlap prevention, compensation, structured errors, and explicit external verification.</p></article>
        </div>
      </section>

      <section className="recruiter-cta">
        <div><p className="recruiter-kicker light">Review the implementation</p><h2>Every visible result maps to a typed service, policy, or database constraint.</h2></div>
        <div className="cta-actions"><a href={GITHUB_URL} target="_blank" rel="noreferrer">Open GitHub <ArrowRight size={17} /></a><Link href="/login">Staff sign in</Link></div>
      </section>

      <footer className="recruiter-footer"><span>Marina Smile AI Receptionist</span><span>Secure AI automation for dental operations</span></footer>
    </main>
  );
}
