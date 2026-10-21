import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RecruiterDemo } from "@/components/recruiter-demo";


function jsonResponse(payload: unknown, ok = true): Response {
  return {
    ok,
    json: async () => payload,
  } as Response;
}

describe("RecruiterDemo", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("shows live aggregate status and a grounded query result", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({
        api: "online",
        supabase: "online",
        knowledge: "ready",
        active_dentists: 3,
        active_services: 8,
        ready_documents: 1,
      }))
      .mockResolvedValueOnce(jsonResponse({
        mode: "knowledge",
        status: "answered",
        answer: "Whitening starts from the stored clinic price.",
        grounding: "rag",
        latency_ms: 421,
        sources: [{ filename: "clinic.pdf", page_number: 2, similarity: 0.91 }],
        slots: [],
      }));

    render(<RecruiterDemo />);
    expect(await screen.findByText("Live services connected")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "How much is teeth whitening?" }));
    await userEvent.click(screen.getByRole("button", { name: "Run live query" }));
    expect(await screen.findByText("Whitening starts from the stored clinic price.")).toBeInTheDocument();
    expect(screen.getByText("Verified from: PDF knowledge base via pgvector")).toBeInTheDocument();
    expect(screen.getByText(/clinic\.pdf · page 2 · 91% match/)).toBeInTheDocument();
  });

  it("switches to the read-only availability example", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({}, false));
    render(<RecruiterDemo />);

    await userEvent.click(screen.getByRole("tab", { name: "Live availability" }));
    expect(screen.getByLabelText("Ask the running system")).toHaveValue(
      "Find the next available teeth cleaning appointment",
    );
  });

  it("offers recruiter-friendly questions from different data paths", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({}, false));
    render(<RecruiterDemo />);

    await userEvent.click(screen.getByRole("button", { name: "Which doctors are available?" }));
    expect(screen.getByLabelText("Ask the running system")).toHaveValue(
      "Which doctors are available?",
    );
    expect(screen.getByRole("button", { name: "Which services are offered by Dr. Sara Haddad?" })).toBeInTheDocument();
  });
});
