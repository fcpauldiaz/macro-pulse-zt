import type { ReactNode } from "react";

type StatCardProps = {
  label: string;
  value: string;
  hint?: string;
  accent?: "orange" | "blue" | "teal" | "neutral";
};

const accentStyles: Record<NonNullable<StatCardProps["accent"]>, string> = {
  orange: "border-[color:var(--accent-orange)] text-[color:var(--accent-orange)]",
  blue: "border-[color:var(--accent-momentum)] text-[color:var(--accent-momentum)]",
  teal: "border-[color:var(--accent-quant)] text-[color:var(--accent-quant)]",
  neutral: "border-[color:var(--border-strong)] text-[color:var(--text-muted)]",
};

export function StatCard({
  label,
  value,
  hint,
  accent = "neutral",
}: StatCardProps) {
  return (
    <article className="panel stat-card">
      <p className="stat-label">{label}</p>
      <p className={`stat-value ${accentStyles[accent]}`}>{value}</p>
      {hint ? <p className="stat-hint">{hint}</p> : null}
    </article>
  );
}

export function SectionHeader({
  kicker,
  title,
  meta,
  action,
}: {
  kicker: string;
  title: string;
  meta?: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-header">
      <div>
        <p className="section-kicker">{kicker}</p>
        <h2 className="section-title">{title}</h2>
        {meta ? <p className="section-meta">{meta}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="empty-state">
      <p>{message}</p>
    </div>
  );
}
