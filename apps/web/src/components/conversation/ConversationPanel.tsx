"use client";

import { useState } from "react";

import type { Message, Source } from "@/lib/api/client";
import { ask } from "@/lib/api/client";

/**
 * The follow-up conversation (`REQ-WORK-007`, `REQ-CONV-001..008`).
 *
 * The workspace's promise is that a reader can interrogate the research rather
 * than only read it, and this is where that happens. Three things it does
 * differently from an ordinary chat, all of them because this is research:
 *
 * - **Answers are typed** (`REQ-CONV-008 AC-2`). The same fact / analysis /
 *   forecast / uncertainty distinction the report carries, so "the evidence
 *   does not say" reads as an answer rather than as a failure.
 * - **Answers cite** (`AC-1`). The sources behind an answer are listed under
 *   it, resolving to the same evidence the report cites.
 * - **The reader is told when research is needed** (`REQ-CONV-003`). The agent
 *   says so rather than answering thinly, and never claims to have researched
 *   something it has not.
 *
 * Context is not held here. Every turn is a row before it is rendered, so
 * `REQ-CONV-001 AC-3` — context survives a reload — is true because there is
 * no client state to lose.
 */

type Props = {
  sessionId: string;
  initialMessages: Message[];
  sourceForEvidence: Map<string, Source>;
  /** Keyed by *evidence* id, not source id: an answer cites evidence, and the
      source behind it is what the reader needs to see. */
};

const ANSWER_WORD: Record<string, string> = {
  fact: "Fact",
  analysis: "Analysis",
  forecast: "Forecast",
  uncertainty: "Uncertain",
};

const EXAMPLES = [
  "Why do you think growth is strong?",
  "Explain this like I'm new to investing.",
  "Find newer information about hiring.",
];

export function ConversationPanel({
  sessionId,
  initialMessages,
  sourceForEvidence,
}: Props) {
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || pending) return;

    setPending(true);
    setFailed(null);
    try {
      const turn = await ask(sessionId, trimmed);
      setMessages((current) => [...current, turn.question, turn.answer]);
      setQuestion("");
    } catch {
      // `REQ-SEC-010`: the reason stays internal. What the reader needs is
      // that it did not go through and their question is still theirs to
      // retry, not a stack trace.
      setFailed("That did not go through. Try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="conversation-heading" className="flex flex-col gap-6">
      <h2 id="conversation-heading" className="display-s text-ink">
        Ask about this research
      </h2>

      {messages.length === 0 ? (
        <div className="flex flex-col gap-3">
          <p className="measure text-micro text-ink-muted">
            The research context is kept, so you do not need to restate the
            subject.
          </p>
          <ul className="flex flex-col gap-2">
            {EXAMPLES.map((example) => (
              <li key={example}>
                <button
                  type="button"
                  onClick={() => void send(example)}
                  disabled={pending}
                  className="text-left text-micro text-ochre-deep underline decoration-line-strong underline-offset-4 disabled:opacity-50"
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <ol className="flex flex-col gap-6">
          {messages.map((message) => (
            <li key={message.id}>
              <Turn message={message} sourceForEvidence={sourceForEvidence} />
            </li>
          ))}
        </ol>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void send(question);
        }}
        className="flex flex-col gap-3"
      >
        <label htmlFor="follow-up" className="text-micro text-ink-muted">
          Your question
        </label>
        <textarea
          id="follow-up"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={3}
          disabled={pending}
          className="w-full rounded-md border border-line bg-surface p-3 text-body text-ink-soft disabled:opacity-60"
          placeholder="Why do you think growth is strong?"
        />
        <div className="flex items-center gap-4">
          <button
            type="submit"
            disabled={pending || !question.trim()}
            className="rounded-md bg-ink px-4 py-2 text-micro text-paper disabled:opacity-40"
          >
            {pending ? "Thinking" : "Ask"}
          </button>
          {failed ? (
            <span role="status" className="text-micro text-ink-muted">
              {failed}
            </span>
          ) : null}
        </div>
      </form>
    </section>
  );
}

function Turn({
  message,
  sourceForEvidence,
}: {
  message: Message;
  sourceForEvidence: Map<string, Source>;
}) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex flex-col gap-1">
        <p className="claim-label">You asked</p>
        <p className="measure text-body text-ink">{message.content}</p>
      </div>
    );
  }

  const typeWord = message.claim_type ? ANSWER_WORD[message.claim_type] : null;

  return (
    <div className="flex flex-col gap-1">
      {/* `REQ-CONV-008 AC-2`: the distinction holds in conversation, so an
          uncertainty is labelled as one rather than reading as a weak fact. */}
      <p className="claim-label">{typeWord ?? "Answer"}</p>
      <p className="measure text-body text-ink-soft">{message.content}</p>

      {message.evidence_ids.length > 0 ? (
        <ul className="mt-2 flex flex-col gap-1">
          {message.evidence_ids.map((id) => {
            const source = sourceForEvidence.get(id);
            return (
              <li key={id} className="text-micro text-ink-muted">
                {source ? source.name : "Cited evidence"}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
