import { AlertTriangle, Inbox, LoaderCircle } from "lucide-react";

export function LoadingState({ label = "Loading clinic data…" }: { label?: string }) {
  return <div className="state-card"><div><LoaderCircle size={24} aria-hidden="true" /><p>{label}</p></div></div>;
}

export function EmptyState({ title, message }: { title: string; message: string }) {
  return <div className="state-card"><div><Inbox size={26} aria-hidden="true" /><p><strong>{title}</strong></p><p>{message}</p></div></div>;
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="state-card" role="alert"><div><AlertTriangle size={26} aria-hidden="true" /><p><strong>Unable to load this view</strong></p><p>{message}</p>{retry ? <button className="secondary-button" onClick={retry}>Try again</button> : null}</div></div>;
}
