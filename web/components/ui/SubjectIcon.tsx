// Subject icons in a small tile: a topic's glyph, an entity's logo or country flag, or a monogram.
// Groups have none.
import {
  Activity,
  ArrowLeftRight,
  Ban,
  Banknote,
  Blocks,
  BrainCircuit,
  Briefcase,
  Building2,
  ChartPie,
  ClipboardCheck,
  CreditCard,
  Fuel,
  Gauge,
  Gavel,
  Gem,
  Globe,
  Handshake,
  Landmark,
  Leaf,
  LifeBuoy,
  Network,
  Percent,
  PiggyBank,
  Scale,
  ScrollText,
  Server,
  Shield,
  Siren,
  TrendingUp,
  Umbrella,
  type LucideIcon,
} from "lucide-react";

import type { Schema } from "@/lib/api/client";
import { initials } from "@/lib/format";

type Ref = Pick<Schema<"SubjectRef">, "name" | "type" | "kind" | "logo" | "icon">;

const TOPIC_GLYPHS: Record<NonNullable<Ref["icon"]>, LucideIcon> = {
  activity: Activity,
  "arrow-left-right": ArrowLeftRight,
  ban: Ban,
  banknote: Banknote,
  blocks: Blocks,
  "brain-circuit": BrainCircuit,
  briefcase: Briefcase,
  "building-2": Building2,
  "chart-pie": ChartPie,
  "clipboard-check": ClipboardCheck,
  "credit-card": CreditCard,
  fuel: Fuel,
  gauge: Gauge,
  gavel: Gavel,
  gem: Gem,
  globe: Globe,
  handshake: Handshake,
  landmark: Landmark,
  leaf: Leaf,
  "life-buoy": LifeBuoy,
  network: Network,
  percent: Percent,
  "piggy-bank": PiggyBank,
  scale: Scale,
  "scroll-text": ScrollText,
  server: Server,
  shield: Shield,
  siren: Siren,
  "trending-up": TrendingUp,
  umbrella: Umbrella,
};

/** Decorative: the subject's name always sits beside it. */
export function SubjectIcon({ subject, size = 16 }: { subject: Ref; size?: 16 | 20 | 28 }) {
  if (subject.type === "topic") {
    const Glyph = subject.icon ? TOPIC_GLYPHS[subject.icon] : null;
    if (!Glyph) return null;
    return (
      <span className={`ent-icon glyph s${size}`} aria-hidden="true">
        <Glyph size={Math.round(size * 0.7)} strokeWidth={size > 20 ? 1.75 : 2} />
      </span>
    );
  }
  if (subject.type !== "entity") return null;
  const flag = subject.kind === "country" ? " flag" : "";
  return (
    <span className={`ent-icon s${size}${flag}`} aria-hidden="true">
      {subject.logo ? (
        // Local logo files need no image optimization.
        <img src={subject.logo} alt="" />
      ) : (
        initials(subject.name).slice(0, 2)
      )}
    </span>
  );
}
