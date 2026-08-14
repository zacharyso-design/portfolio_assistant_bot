import type { ChatAnswer, Citation } from "../../api/contracts";
import { Citations } from "../../components/Citations";

type Props = {
  answer: ChatAnswer | null;
  question: string;
  busy: boolean;
  onQuestionChange: (question: string) => void;
  onSubmit: (event: React.FormEvent) => void;
  onOpenCitation: (citation: Citation) => void;
};

export function ProjectChatPanel({ answer, question, busy, onQuestionChange, onSubmit, onOpenCitation }: Props) {
  const citations = answer?.claims?.flatMap(claim => claim.citations) || [];
  return <section className="panel archive-chat">
    <header><div><small>Uses only this project's evidence</small><h2>Project-scoped assistant</h2></div></header>
    <div className="chat-answer">{answer ? <><p>{answer.answer}</p>{Boolean(answer.evidence_dropped_chunks) && <div className="uncertainty">{answer.evidence_dropped_chunks} matching chunks were outside the configured evidence window.</div>}{answer.uncertainty && <div className="uncertainty">{answer.uncertainty}</div>}{citations.length > 0 && <Citations items={citations} onOpen={onOpenCitation} />}</> : <p className="muted">Ask what changed, what remains unresolved, or what supports a decision.</p>}</div>
    <form className="chat-form" onSubmit={onSubmit}><label htmlFor="archive-project-question">Ask this project</label><textarea id="archive-project-question" value={question} onChange={event => onQuestionChange(event.target.value)} required placeholder="Ask about this project" /><button className="button primary" disabled={busy}>{busy ? "Searching evidence…" : "Send ↑"}</button></form>
  </section>;
}
