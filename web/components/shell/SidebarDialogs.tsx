// The two dialogs the sidebar's foot opens (Sidebar.tsx): Help, which `?` also opens, and Data
// sources; and Priority definitions, which an info button beside a priority opens (`PriorityInfo`).
// Each body fetches only while its dialog is open, since Modal renders children only then.
import { Info } from "lucide-react";
import { useState, type ReactNode } from "react";

import { useDataSources, useMaterialityLevels } from "@/lib/api/queries";
import { COLLECTION_LABEL, longDate, shortDate } from "@/lib/format";
import { ChangeMark, PRIORITIES, PriorityBadge, type Priority } from "@/components/developments/labels";
import { CloseButton, Modal } from "@/components/ui/Modal";

interface DialogProps {
  open: boolean;
  onClose: () => void;
}

function Panel({ id, title, onClose, open, children }: DialogProps & { id: string; title: string; children: ReactNode }) {
  return (
    <Modal open={open} onClose={onClose} labelledBy={id} className="panel">
      <div className="sheet-head">
        <h2 id={id}>{title}</h2>
        <CloseButton onClick={onClose} />
      </div>
      {children}
    </Modal>
  );
}

export function DataSourcesDialog(props: DialogProps) {
  return (
    <Panel {...props} id="sources-title" title="Data sources">
      <SourcesBody />
    </Panel>
  );
}

function SourcesBody() {
  const sources = useDataSources();
  const data = sources.data;
  return (
    <div className="panel-body">
      {data && (
        <>
          <p className="cap">
            {data.total_documents} documents stored
            {data.t0 ? `; the latest run is the week to ${longDate(data.t0)}` : ""}
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Collection</th>
                <th className="num">Documents</th>
                <th className="num">Relevant</th>
                <th>Published</th>
              </tr>
            </thead>
            <tbody>
              {data.sources.map((s) => (
                <tr key={`${s.source}-${s.collection}`}>
                  <td>{s.source.toUpperCase()}</td>
                  <td>{COLLECTION_LABEL[s.collection]}</td>
                  <td className="num">{s.documents}</td>
                  <td className="num">{s.relevant_documents}</td>
                  <td>
                    {shortDate(s.first_published)} to {shortDate(s.last_published)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {sources.isError && <p className="cap">Could not load data sources.</p>}
    </div>
  );
}

const SHORTCUTS: [string[], string][] = [
  [["⌘", "K"], "Search"],
  [["?"], "Help"],
  [["Esc"], "Close a menu, dialog or development"],
];

/** The labels after priority, one per question, in the order a row shows them. */
const LABELS: { label: ReactNode; text: string }[] = [
  {
    label: <span className="adv">Adverse event</span>,
    text: "The news reports harm to the entity itself: something that happened at it, such as an outage, a fraud case or a penalty, or a trend or new rule that hurts it directly. Commentary, forecasts, share price moves and follow-up reports do not count.",
  },
  { label: <span>Negative tone</span>, text: "How the news reads for the entity: negative, neutral or positive." },
  { label: <ChangeMark change="new" />, text: "Added in the latest weekly run." },
  { label: <ChangeMark change="updated" />, text: "Seen before, with new reports in the latest weekly run." },
];

/**
 * Help: what the labels on a development mean, and the keyboard shortcuts. Each priority's text is
 * the team's definition from `GET /materiality-levels` (slide 7's, in pipeline/params.py).
 */
export function HelpDialog(props: DialogProps) {
  return (
    <Panel {...props} id="help-title" title="Help">
      <HelpBody />
    </Panel>
  );
}

function HelpBody() {
  const levels = useMaterialityLevels();
  const definition = (p: Priority) =>
    levels.data?.find((l) => l.name === p)?.description ?? (levels.isError ? "Could not load this definition." : "");
  return (
    <div className="panel-body help">
      <section aria-labelledby="help-labels">
        <h3 id="help-labels">
          Labels on a development <PriorityInfo />
        </h3>
        {PRIORITIES.map((p) => (
          <div key={p} className="help-row">
            <span>
              <PriorityBadge priority={p} />
            </span>
            <span>{definition(p)}</span>
          </div>
        ))}
        {LABELS.map((l, i) => (
          <div key={i} className="help-row">
            <span>{l.label}</span>
            <span>{l.text}</span>
          </div>
        ))}
      </section>
      <section aria-labelledby="help-keys">
        <h3 id="help-keys">Keyboard shortcuts</h3>
        <dl className="keys">
          {SHORTCUTS.map(([combo, what]) => (
            <div key={what} style={{ display: "contents" }}>
              <dt>
                {combo.map((k) => (
                  <kbd key={k} className="kbd">
                    {k}
                  </kbd>
                ))}
              </dt>
              <dd>{what}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

/** An info button that opens Priority definitions; it sits beside a priority badge or a heading. */
export function PriorityInfo() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="btn-icon prio-info" title="Priority definitions" aria-label="Priority definitions" onClick={() => setOpen(true)}>
        <Info size={14} aria-hidden="true" />
      </button>
      <PriorityDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}

const PRIORITY_TINT: Record<Priority, string> = { High: "attn", Medium: "watch", Low: "low" };

/** The steps from a source to a priority, as pipeline/run.py runs them each week. */
const STEPS = [
  "Each week news, official, research and data sources are collected, and Claude screens every document for the entities and topics it is about.",
  "Claude extracts findings from each relevant document, each with a quote checked against the text, and findings that report the same event are merged into one development. It is shown once two news outlets, one reputable outlet or an official, research or data source report it; until then it is Unconfirmed.",
  "Claude rates the development High, Medium or Low for each entity it is about, with a one-sentence reason. A topic takes the highest priority among the development's entities.",
  "A development adverse for an entity also reaches the entities linked to it, at a level set by its priority and how strongly the link carries news in that direction.",
];

/**
 * Priority definitions, after the team's design of 23 Nov 2026: a card per priority with its
 * definition and examples from `GET /materiality-levels` (PRIORITY_DEFINITIONS in pipeline/params.py,
 * after the priority prompt), then how a priority is set.
 */
export function PriorityDialog(props: DialogProps) {
  return (
    <Modal open={props.open} onClose={props.onClose} labelledBy="prio-title" className="panel prio">
      <div className="prio-head">
        <div>
          <p className="prio-kicker">Priority framework</p>
          <h2 id="prio-title">Priority definitions</h2>
          <p>How much a development matters to each entity it is about, judged only from what its sources show for Singapore.</p>
        </div>
        <CloseButton onClick={props.onClose} />
      </div>
      <PriorityBody />
    </Modal>
  );
}

function PriorityBody() {
  const levels = useMaterialityLevels();
  return (
    <div className="panel-body prio-body">
      {levels.isError && <p className="cap">Could not load the definitions.</p>}
      <div className="prio-cards">
        {PRIORITIES.map((p) => {
          const level = levels.data?.find((l) => l.name === p);
          return (
            <section key={p} className={`prio-card ${PRIORITY_TINT[p]}`} aria-label={p}>
              <h3>{p}</h3>
              <p>{level?.description}</p>
              {level && <p className="prio-ex">Examples: {level.examples}</p>}
            </section>
          );
        })}
      </div>
      <section className="prio-how" aria-labelledby="prio-how">
        <h3 id="prio-how">How a priority is set</h3>
        <p>
          Only what the sources show for Singapore counts: the entity&apos;s Singapore unit or services, or Singapore&apos;s financial system. Where
          no Singapore link is stated or clearly implied, none is assumed, and no outside knowledge is used.
        </p>
        <ol>
          {STEPS.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </section>
    </div>
  );
}
