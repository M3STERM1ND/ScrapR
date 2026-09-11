"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError, createResearch } from "@/lib/api/client";
import { ArrowRight } from "@/components/ui/primitives";

/**
 * The intake form (`REQ-INPUT-001..006`).
 *
 * One required field and three optional ones, because the product's promise is
 * that you ask a question — not that you fill in a brief. The optional context
 * is folded away until asked for, so the page reads as one question mark rather
 * than a form to complete.
 *
 * Validation is client-side for the immediate feedback and server-side for the
 * truth; the server's message is what is displayed if the two ever disagree.
 */

const OBJECTIVE_MIN = 10;
const OBJECTIVE_MAX = 2000;

const EXAMPLES = [
  "How is Anthropic positioned against OpenAI in enterprise AI?",
  "What is driving margin compression at Chipotle this year?",
  "Which companies are hiring hardest in humanoid robotics?",
];

export function ObjectiveForm() {
  const router = useRouter();

  const [objective, setObjective] = useState("");
  const [showContext, setShowContext] = useState(false);
  const [company, setCompany] = useState("");
  const [url, setUrl] = useState("");
  const [instructions, setInstructions] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = objective.trim();
  const tooShort = trimmed.length > 0 && trimmed.length < OBJECTIVE_MIN;
  const canSubmit = trimmed.length >= OBJECTIVE_MIN && !submitting;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setError(null);

    try {
      const created = await createResearch({
        objective: trimmed,
        instructions: instructions.trim() || null,
        context_company: company.trim() || null,
        context_url: url.trim() || null,
        context_ticker: null,
      });
      router.push(`/research/${created.session_id}`);
    } catch (cause) {
      // The API's message is the only text safe to show; anything else it knows
      // stays on the server.
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Could not reach the service. Check your connection and try again.",
      );
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mt-12">
      <label htmlFor="objective" className="claim-label block">
        Your question
      </label>

      <div className="well mt-3 transition-shadow duration-200 focus-within:shadow-soft">
        <textarea
          id="objective"
          name="objective"
          rows={3}
          value={objective}
          onChange={(event) => setObjective(event.target.value)}
          maxLength={OBJECTIVE_MAX}
          autoFocus
          placeholder="Ask anything that needs evidence behind the answer."
          aria-describedby="objective-help"
          className="w-full resize-none bg-transparent px-5 py-4 text-lead text-ink outline-none placeholder:text-ink-muted/70"
        />
        <div className="flex items-center justify-between border-t border-line px-5 py-3">
          <p id="objective-help" className="text-micro text-ink-muted">
            {tooShort
              ? `A few more words. ${OBJECTIVE_MIN - trimmed.length} to go.`
              : "The more specific the question, the better the report."}
          </p>
          <p className="tnum text-micro text-ink-muted">
            {trimmed.length}/{OBJECTIVE_MAX}
          </p>
        </div>
      </div>

      <div className="mt-5">
        <button
          type="button"
          onClick={() => setShowContext((open) => !open)}
          aria-expanded={showContext}
          aria-controls="context-fields"
          className="text-small text-ink-muted underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink"
        >
          {showContext ? "Hide extra context" : "Add context (optional)"}
        </button>

        {showContext ? (
          <div id="context-fields" className="mt-5 grid gap-4 sm:grid-cols-2">
            <Field
              id="company"
              label="Company or subject"
              value={company}
              onChange={setCompany}
              placeholder="Acme Corp"
            />
            <Field
              id="url"
              label="A page to start from"
              value={url}
              onChange={setUrl}
              placeholder="https://"
              type="url"
            />
            <div className="sm:col-span-2">
              <Field
                id="instructions"
                label="Anything ScrapR should know"
                value={instructions}
                onChange={setInstructions}
                placeholder="Focus on the last two quarters."
              />
            </div>
          </div>
        ) : null}
      </div>

      {error ? (
        <p
          role="alert"
          className="mt-6 border-l-2 border-ochre-deep pl-4 text-small text-ink-soft"
        >
          {error}
        </p>
      ) : null}

      <div className="mt-8 flex flex-wrap items-center gap-5">
        <button
          type="submit"
          disabled={!canSubmit}
          className="group inline-flex h-12 items-center justify-center gap-2 rounded-sm bg-ink px-6 text-small font-medium text-paper shadow-soft transition-[background-color,box-shadow,transform] duration-200 ease-out hover:bg-ochre-deep hover:shadow-lift active:translate-y-px disabled:cursor-not-allowed disabled:bg-line-strong disabled:text-ink-muted disabled:shadow-none"
        >
          {submitting ? "Starting research" : "Start research"}
          {submitting ? null : <ArrowRight />}
        </button>
        <p className="text-micro text-ink-muted">
          No account needed. Research starts immediately.
        </p>
      </div>

      <div className="mt-14">
        <p className="claim-label">Or start from one of these</p>
        <ul className="mt-4 flex flex-col gap-px">
          {EXAMPLES.map((example) => (
            <li key={example}>
              <button
                type="button"
                onClick={() => setObjective(example)}
                className="group flex w-full items-center justify-between gap-6 border-b border-line py-4 text-left text-small text-ink-soft transition-colors duration-200 hover:text-ink"
              >
                <span>{example}</span>
                <ArrowRight className="text-ink-muted transition-colors group-hover:text-ochre-deep" />
              </button>
            </li>
          ))}
        </ul>
      </div>
    </form>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="claim-label block">
        {label}
      </label>
      <input
        id={id}
        name={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="well mt-2 w-full bg-sunk px-4 py-3 text-small text-ink outline-none transition-shadow duration-200 placeholder:text-ink-muted/70 focus:shadow-soft"
      />
    </div>
  );
}
