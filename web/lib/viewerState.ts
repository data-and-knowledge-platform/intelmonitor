// Per-viewer state kept in this browser: the Personal watchlist, Recently viewed and the current
// view. This module is the only code that touches localStorage. It keeps the parsed state in memory,
// serves it through useSyncExternalStore and follows other tabs through the `storage` event. The
// hooks resolve ids to subjects from GET /subjects, so ids that left the seed (and groups) are
// dropped in one place and components never see raw ids.
//
// Ways the stored state can be wrong, and what each reads as:
// - The key is missing (first visit, cleared site data): every field at its default.
// - The value is not JSON, or JSON that is not an object (array, null, number, string): defaults.
// - A field is missing: its default. A missing `watchlist` means never edited and reads as MAS;
//   `[]` means emptied and stays empty.
// - A field has the wrong type (`watchlist` or `recent` not an array of strings, `view` not a
//   non-empty string): that field alone reads as its default and is overwritten on its next write.
// - Repeated ids: kept once. `recent` longer than 5: cut to the newest 5.
// - Ids that left the seed, or group ids: hidden by the hooks once /subjects loads, and left out the
//   next time that field is written. Nothing is dropped before /subjects loads.
// - A view that names no function the API serves: Personal. While /functions loads, the stored view
//   holds; if it fails, Personal.
// - localStorage itself throws on access (blocked site data): state lives in memory for this tab,
//   from the defaults.
// - setItem throws (blocked storage, quota): this tab keeps its state in memory from then on and
//   stops following storage; the first watchlist change that cannot be saved raises one notice.
// - Two tabs write: every write re-reads storage and changes only its own field, and the other tab
//   picks the change up from the `storage` event (a `clear()` arrives with no key and re-reads too).
// - The retired `im.sidebar.closed` key: removed once on load.
import { useEffect, useMemo, useSyncExternalStore } from "react";

import type { Schema } from "@/lib/api/client";
import { useFunctions, useSubjects } from "@/lib/api/queries";
import { useToast } from "@/components/ui/Toast";

type Subject = Schema<"SubjectSummary">;
type FunctionDetail = Schema<"FunctionDetail">;

/** An entity or topic id from api/seed/subjects.yaml. */
export type SubjectId = string;

/** A view: "personal", or a function's id. */
export type ViewId = string;

export const PERSONAL: ViewId = "personal";

/** localStorage["im.state.v1"], validated. A missing field reads as its default. */
interface Stored {
  /** Personal watchlist ids, unordered. Missing: never edited, reads as MAS. */
  watchlist?: SubjectId[];
  /** Entity and topic pages opened, newest first, no repeats, at most 5. */
  recent?: SubjectId[];
  /** The view last chosen. */
  view?: ViewId;
}

const KEY = "im.state.v1";
const RETIRED_KEY = "im.sidebar.closed";
const DEFAULT_WATCHLIST: SubjectId[] = ["mas"];
const RECENT_MAX = 5;

/** An array of strings, each kept once; anything else is invalid. */
function ids(value: unknown): SubjectId[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out: SubjectId[] = [];
  for (const v of value) {
    if (typeof v !== "string") return undefined;
    if (!out.includes(v)) out.push(v);
  }
  return out;
}

/** Reads a stored value field by field; an invalid field is left out, so it reads as its default. */
function parse(raw: string | null): Stored {
  if (raw === null) return {};
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return {};
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) return {};
  const watchlist = ids(Reflect.get(value, "watchlist"));
  const recent = ids(Reflect.get(value, "recent"))?.slice(0, RECENT_MAX);
  const view: unknown = Reflect.get(value, "view");
  return {
    ...(watchlist && { watchlist }),
    ...(recent && { recent }),
    ...(typeof view === "string" && view && { view }),
  };
}

/** localStorage, or null where touching it throws. */
function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

// False once storage cannot be read or written: this tab then keeps its state in memory only.
let persistent = true;
// Whether this tab has shown the notice that the watchlist cannot be saved.
let noticed = false;
// The seed's entity and topic ids once /subjects has loaded; writes leave other ids out.
let known: ReadonlySet<SubjectId> | null = null;

/** The stored state, or null when storage cannot be read. */
function readStorage(): Stored | null {
  const s = storage();
  if (!s) return null;
  try {
    return parse(s.getItem(KEY));
  } catch {
    return null;
  }
}

function load(): Stored {
  try {
    storage()?.removeItem(RETIRED_KEY);
  } catch {
    // Blocked storage keeps the old key; nothing reads it.
  }
  const stored = readStorage();
  if (!stored) persistent = false;
  return stored ?? {};
}

let state: Stored = load();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

window.addEventListener("storage", (e) => {
  if (!persistent || (e.key !== KEY && e.key !== null)) return;
  state = readStorage() ?? state;
  emit();
});

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

/** Drops ids outside the seed, once the seed is known. */
const prune = (list: SubjectId[]) => (known ? list.filter((id) => known?.has(id)) : list);

/**
 * Changes one field from its freshly re-read value, leaving the others as stored. Returns whether
 * the change was saved; when storage throws, the change lives in this tab's memory only.
 */
function update<K extends keyof Stored>(key: K, change: (current: Stored[K]) => Stored[K]): boolean {
  const base = (persistent && readStorage()) || state;
  const value = change(base[key]);
  // An unchanged field is not written, so other tabs see no event.
  if (JSON.stringify(value) === JSON.stringify(base[key])) return persistent;
  const next: Stored = { ...base, [key]: value };
  if (persistent) {
    try {
      storage()?.setItem(KEY, JSON.stringify(next));
    } catch {
      persistent = false;
    }
  }
  state = next;
  emit();
  return persistent;
}

const useStored = () => useSyncExternalStore(subscribe, () => state);

/** The seed's entities and topics by id; undefined until /subjects loads. */
function useSeed() {
  const data = useSubjects().data;
  const seed = useMemo(() => data && seedOf(data), [data]);
  useEffect(() => {
    if (seed) known = new Set(seed.keys());
  }, [seed]);
  return seed;
}

const seedOf = (subjects: Subject[]) => new Map(subjects.filter((s) => s.type !== "group").map((s) => [s.id, s]));

const resolve = (list: SubjectId[], seed: Map<SubjectId, Subject> | undefined) => list.flatMap((id) => seed?.get(id) ?? []);

/** A view with its display name. */
export interface View {
  id: ViewId;
  name: string;
}

/** The current view: the stored one while functions load, Personal when it names no function. */
function viewOf(stored: Stored, functions: FunctionDetail[] | undefined): View {
  const id = stored.view ?? PERSONAL;
  if (id === PERSONAL) return { id, name: "Personal" };
  if (!functions) return { id, name: "" };
  const fn = functions.find((f) => f.id === id);
  return fn ? { id: fn.id, name: fn.name } : { id: PERSONAL, name: "Personal" };
}

/** A view's watchlist: entities, then topics, each alphabetical. */
export interface Watchlist {
  view: View;
  entities: Subject[];
  topics: Subject[];
  /** Only the Personal watchlist is edited; a function view is a fixed preset. */
  editable: boolean;
}

function watchlistOf(stored: Stored, subjects: Subject[] | undefined, functions: FunctionDetail[] | undefined): Watchlist {
  const view = viewOf(stored, functions);
  const editable = view.id === PERSONAL;
  const preset = functions?.find((f) => f.id === view.id)?.watchlist;
  const list = editable ? (stored.watchlist ?? DEFAULT_WATCHLIST) : preset ? [...preset.entities, ...preset.topics] : [];
  const shown = resolve(list, subjects && seedOf(subjects)).sort((a, b) => a.name.localeCompare(b.name));
  return {
    view,
    entities: shown.filter((s) => s.type === "entity"),
    topics: shown.filter((s) => s.type === "topic"),
    editable,
  };
}

/** Whether the current view is Personal, so its watchlist needs no /functions. */
export const inPersonalView = () => (state.view ?? PERSONAL) === PERSONAL;

/** The current view's watchlist, for code outside React such as the home route. */
export const currentWatchlist = (subjects: Subject[], functions: FunctionDetail[]) => watchlistOf(state, subjects, functions);

/** The functions for resolving a view: none while loading, [] once the request fails. */
function useFunctionList() {
  const functions = useFunctions();
  return functions.isError ? [] : functions.data;
}

/** The current view, every view in switcher order (Personal, then the five functions), and a setter. */
export function useView() {
  const stored = useStored();
  const functions = useFunctionList();
  const views: View[] = [{ id: PERSONAL, name: "Personal" }, ...(functions ?? []).map((f) => ({ id: f.id, name: f.name }))];
  return { view: viewOf(stored, functions), views, setView: (id: ViewId) => void update("view", () => id) };
}

/** The current view's watchlist: Personal from storage, a function view from its preset. */
export function useWatchlist(): Watchlist {
  const stored = useStored();
  const functions = useFunctionList();
  const subjects = useSubjects().data;
  useSeed();
  return watchlistOf(stored, subjects, functions);
}

/** For "Add to watchlist": always the Personal watchlist, whatever the view. */
export function usePersonalWatchlist() {
  const list = useStored().watchlist ?? DEFAULT_WATCHLIST;
  useSeed();
  const toast = useToast();
  const change = (edit: (current: SubjectId[]) => SubjectId[]) => {
    const saved = update("watchlist", (current) => edit(prune(current ?? DEFAULT_WATCHLIST)));
    if (saved || noticed) return;
    noticed = true;
    toast({ message: "This browser blocks storage, so watchlist changes last only until this tab closes.", duration: 8000 });
  };
  return {
    has: (id: SubjectId) => list.includes(id),
    add: (id: SubjectId) => change((current) => [...current.filter((x) => x !== id), id]),
    remove: (id: SubjectId) => change((current) => current.filter((x) => x !== id)),
  };
}

/** Entity and topic pages opened in this browser, newest first, shared by every view. */
export function useRecentlyViewed(): Subject[] {
  const stored = useStored();
  return resolve(stored.recent ?? [], useSeed());
}

/** Called by an entity or topic page once its subject has loaded: moves it to the front of Recently viewed. */
export function recordView(id: SubjectId) {
  update("recent", (current) => [id, ...prune(current ?? []).filter((x) => x !== id)].slice(0, RECENT_MAX));
}
