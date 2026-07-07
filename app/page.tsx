"use client";

import { useRef, useState } from "react";

type Step = {
  module: string;
  pattern?: string;
  prompt: { system_prompt: string; user_prompt: string };
  response: unknown;
};
type QuestionResult = {
  id: string;
  title: string;
  score: number;
  max: number;
  status: "ok" | "partial" | "escalate";
  mark: string;
  feedback: string;
};
type ExecuteResult = {
  status: "ok" | "error";
  error: string | null;
  response: string | null;
  steps: Step[];
  meta?: {
    total: number;
    max: number;
    questions: QuestionResult[];
    source: string;
    mode: "full" | "subset" | "general";
    instructions: string;
  };
};

type Kind = "image" | "pdf" | "word" | "other";

function kindOf(name: string): Kind {
  const n = name.toLowerCase();
  if (/\.(png|jpe?g|webp|gif)$/.test(n)) return "image";
  if (n.endsWith(".pdf")) return "pdf";
  if (/\.(docx?|rtf)$/.test(n)) return "word";
  return "other";
}
const ACCEPT = ".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp";

const statusLabel: Record<QuestionResult["status"], string> = {
  ok: "full credit",
  partial: "partial",
  escalate: "escalate",
};

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [instructions, setInstructions] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ExecuteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function pick(f: File | null) {
    setResult(null);
    setError(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    if (!f) {
      setFile(null);
      setPreviewUrl(null);
      return;
    }
    setFile(f);
    setPreviewUrl(URL.createObjectURL(f));
  }

  async function grade() {
    if (!file) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      if (instructions.trim()) fd.append("instructions", instructions.trim());
      const res = await fetch("/api/execute", { method: "POST", body: fd });
      const data: ExecuteResult = await res.json();
      if (data.status === "error") {
        setError(data.error || "The agent returned an error.");
      } else {
        setResult(data);
      }
    } catch {
      setError("Could not reach /api/execute. Check the server and try again.");
    } finally {
      setLoading(false);
    }
  }

  const meta = result?.meta;
  const kind = file ? kindOf(file.name) : "other";
  const escalated = meta?.questions.filter((q) => q.status === "escalate").length ?? 0;

  return (
    <div className="page">
      <header className="masthead">
        <div className="brandrow">
          <div className="logomark">♞</div>
          <div className="brandname">CheckMate</div>
        </div>
        <p className="tagline">
          An autonomous agent focused on grading <strong>Calculus&nbsp;1 (Hedva&nbsp;1)</strong> exams.
          Upload a scanned exam — CheckMate reads every page, grades each question on the
          student&apos;s own method, and returns the exam marked up with per-question feedback,
          plus the full execution trace.
        </p>
        <div className="metabar">
          <span className="pill">
            <a href="/api/team_info" target="_blank" rel="noreferrer">GET /api/team_info</a>
          </span>
          <span className="pill">
            <a href="/api/agent_info" target="_blank" rel="noreferrer">GET /api/agent_info</a>
          </span>
          <span className="pill">
            <a href="/api/model_architecture" target="_blank" rel="noreferrer">GET /api/model_architecture</a>
          </span>
          <span className="pill">POST /api/execute</span>
        </div>
      </header>

      <section className="card">
        <p className="kicker">Grade an exam</p>
        <h2>Upload the scanned exam</h2>
        <p className="hint">
          PDF, Word (.doc/.docx), or images. In this demo build the grading is simulated —
          the annotated result and full <code>steps[]</code> trace are shaped exactly as the
          real pipeline returns them.
        </p>

        <div
          className={"dropzone" + (drag ? " over" : "") + (file ? " has" : "")}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            if (e.dataTransfer.files?.[0]) pick(e.dataTransfer.files[0]);
          }}
          role="button"
          tabIndex={0}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            hidden
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
          />
          {!file ? (
            <>
              <div className="dzicon">⇪</div>
              <div className="dztitle">Drop exam scan here</div>
              <div className="dzsub">or click to browse — PDF · Word · images</div>
            </>
          ) : (
            <div className="filechip" onClick={(e) => e.stopPropagation()}>
              <span className="fileic">{kind === "image" ? "🖼" : kind === "pdf" ? "📄" : "📝"}</span>
              <span className="filemeta">
                <span className="filename">{file.name}</span>
                <span className="filesize">{Math.round(file.size / 1024)} KB · {kind.toUpperCase()}</span>
              </span>
              <button
                className="filex"
                onClick={() => {
                  pick(null);
                  if (inputRef.current) inputRef.current.value = "";
                }}
                aria-label="Remove file"
              >
                ✕
              </button>
            </div>
          )}
        </div>

        <div className="instrblock">
          <label className="instrlabel" htmlFor="instr">
            Specific instructions <span className="opt">(optional)</span>
          </label>
          <textarea
            id="instr"
            className="instr"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="e.g. &quot;Grade only Q3 and Q4&quot; · &quot;General feedback on the exam, no scoring&quot;. Leave empty to grade the whole exam."
          />
        </div>

        <div className="controls">
          <button className="btn" onClick={grade} disabled={loading || !file}>
            {loading ? (
              <>
                <span className="spin" />
                Grading…
              </>
            ) : (
              "Grade exam"
            )}
          </button>
        </div>
      </section>

      {error && <div className="error">⚠ {error}</div>}

      {result && result.status === "ok" && meta && (
        <>
          {meta.mode === "general" ? (
            <section className="card">
              <p className="kicker">Result — general assessment</p>
              <div className="review">
                <div className="scanwrap">
                  <div className="scanview">
                    {kind === "image" && previewUrl && (
                      <img src={previewUrl} alt="Uploaded exam scan" className="scanimg" />
                    )}
                    {kind === "pdf" && previewUrl && (
                      <iframe src={previewUrl} title="Uploaded exam scan" className="scanframe" />
                    )}
                    {(kind === "word" || kind === "other") && (
                      <div className="docph">
                        <div className="docic">📝</div>
                        <div>{file?.name}</div>
                      </div>
                    )}
                    <span className="gradedstamp">REVIEWED</span>
                  </div>
                  <p className="scancap">
                    General opinion — no per-question scoring, as requested.
                  </p>
                </div>
                <div className="annots">
                  <div className="annotshead">Assessment</div>
                  <div className="annot partial">
                    <p className="annotfb" style={{ whiteSpace: "pre-wrap" }}>
                      {result.response}
                    </p>
                  </div>
                </div>
              </div>
            </section>
          ) : (
          <section className="card">
            <p className="kicker">Result — graded copy</p>
            <div className="gradehead">
              <div className="scorebadge">
                <span className="big">{meta.total}</span>
                <span className="den">/ {meta.max}</span>
              </div>
              <p className="summary">
                {meta.questions.length} questions graded with partial credit.{" "}
                {escalated > 0
                  ? `${escalated} escalated to the teacher for review.`
                  : "None required human review."}
              </p>
            </div>

            <div className="review">
              {/* The uploaded scan, with grading marks overlaid */}
              <div className="scanwrap">
                <div className="scanview">
                  {kind === "image" && previewUrl && (
                    <img src={previewUrl} alt="Uploaded exam scan" className="scanimg" />
                  )}
                  {kind === "pdf" && previewUrl && (
                    <iframe src={previewUrl} title="Uploaded exam scan" className="scanframe" />
                  )}
                  {(kind === "word" || kind === "other") && (
                    <div className="docph">
                      <div className="docic">📝</div>
                      <div>{file?.name}</div>
                      <div className="docnote">
                        Word documents are graded but not previewed in-browser. The real
                        pipeline returns an annotated PDF.
                      </div>
                    </div>
                  )}
                  {/* Overlaid grading marks (illustrative positions in the demo) */}
                  {(kind === "image" || kind === "pdf") &&
                    meta.questions.map((q, i) => (
                      <span
                        key={q.id}
                        className={"mark " + q.status}
                        style={{
                          top: `${8 + i * 17}%`,
                          [i % 2 ? "left" : "right"]: "5%",
                        } as React.CSSProperties}
                        title={`${q.id}: ${q.mark}`}
                      >
                        {q.status === "ok" ? "✓" : q.mark}
                      </span>
                    ))}
                  <span className="gradedstamp">GRADED · {meta.total}/{meta.max}</span>
                </div>
                <p className="scancap">
                  Grading marks are illustrative in this demo. The real pipeline overlays
                  each mark at its detected position on the page.
                </p>
              </div>

              {/* Margin annotations — one per question */}
              <div className="annots">
                <div className="annotshead">Annotations</div>
                {meta.questions.map((q) => (
                  <div className={"annot " + q.status} key={q.id}>
                    <div className="annothead">
                      <span className="annotq">{q.title}</span>
                      <span className="annotright">
                        <span className={"tag " + (q.status === "ok" ? "ok" : q.status === "partial" ? "partial" : "escalate")}>
                          {statusLabel[q.status]}
                        </span>
                        <span className="qscore">{q.score}/{q.max}</span>
                      </span>
                    </div>
                    <p className="annotfb">{q.feedback}</p>
                  </div>
                ))}
              </div>
            </div>
          </section>
          )}

          <section className="card">
            <p className="kicker">Execution trace</p>
            <h2>steps[] — every call the agent made</h2>
            <p className="hint">
              {result.steps.length} steps, in order — module, prompt, and response. Names
              match the architecture diagram: Reader, Retriever, Grader, Reflector.
            </p>
            {result.steps.map((s, i) => (
              <details className="step" key={i}>
                <summary>
                  <span className="stepnum">{i + 1}</span>
                  <span className="stepmod">{s.module}</span>
                  {s.pattern && <span className="steppat">· {s.pattern}</span>}
                  <span className="chev">▸</span>
                </summary>
                <div className="stepbody">
                  <div className="field">
                    <div className="label">System prompt</div>
                    <pre className="code">{s.prompt.system_prompt}</pre>
                  </div>
                  <div className="field">
                    <div className="label">User prompt</div>
                    <pre className="code">{s.prompt.user_prompt}</pre>
                  </div>
                  <div className="field">
                    <div className="label">Response</div>
                    <pre className="code">{JSON.stringify(s.response, null, 2)}</pre>
                  </div>
                </div>
              </details>
            ))}
          </section>
        </>
      )}

      <p className="foot">
        CheckMate · <em>Modern AI Agents</em> course · demo build (grading simulated).
        <br />
        Real pipeline: OCR · Pinecone RAG · gpt-5.4-mini · $13 budget.
      </p>
    </div>
  );
}
