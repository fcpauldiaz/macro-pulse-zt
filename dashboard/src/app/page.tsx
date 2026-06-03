import { PerformanceTable } from "@/components/PerformanceTable";
import { SignalTable } from "@/components/SignalTable";
import { EmptyState, SectionHeader, StatCard } from "@/components/ui";
import { getDashboardData } from "@/lib/queries";

export const dynamic = "force-dynamic";

function formatPct(value: number | null): string {
  if (value === null) {
    return "—";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

export default async function HomePage() {
  const data = await getDashboardData();

  if (!data.snapshot) {
    return (
      <main className="page-shell">
        <EmptyState message="No snapshots yet. Run the Coolify scheduled task (/bin/bash /app/scripts/daily-sync.sh) or `python -m scraper.scrape_pulse sync` to ingest today's signals." />
      </main>
    );
  }

  const { snapshot, stats } = data;

  return (
    <main className="page-shell">
      <section className="hero">
        <div className="hero-top">
          <div>
            <p className="hero-kicker">MacroPulse daily tracker</p>
            <h1 className="hero-title">
              Ready-to-buy <span>performance</span>
            </h1>
            <p className="hero-meta">
              Snapshot {snapshot.snapshotDate}
              {snapshot.pulseUpdatedAt
                ? ` · Pulse updated ${new Date(snapshot.pulseUpdatedAt).toLocaleString()}`
                : null}
            </p>
          </div>
          <span className="regime-badge">{snapshot.regime}</span>
        </div>

        <div className="stats-grid">
          <StatCard
            label="Momentum ready"
            value={String(snapshot.momentumCount)}
            hint="Señales de Momentum"
            accent="blue"
          />
          <StatCard
            label="Quant ready"
            value={String(snapshot.quantCount)}
            hint="Quant Score ready to buy"
            accent="teal"
          />
          <StatCard
            label="Open positions"
            value={String(stats.openCount)}
            hint={`Avg return ${formatPct(stats.avgOpenReturn)}`}
            accent="orange"
          />
          <StatCard
            label="Closed trades"
            value={String(stats.closedCount)}
            hint={`Win rate ${formatPct(stats.winRate)}`}
            accent="neutral"
          />
        </div>
      </section>

      <SectionHeader
        kicker="Today's signals"
        title="Ready to buy tables"
        meta="Momentum and Quant Score lists synced from MacroPulse"
      />

      <div className="signals-grid">
        <SignalTable
          title="Señales de Momentum"
          subtitle="Ready to Buy"
          accentClass="momentum"
          rows={data.momentumSignals}
          mode="momentum"
        />
        <SignalTable
          title="Señales de Quant Score"
          subtitle="Ready to Buy"
          accentClass="quant"
          rows={data.quantSignals}
          mode="quant"
        />
      </div>

      <SectionHeader
        kicker="Performance"
        title="Tracked trade positions"
        meta="Entry price captured on first ready-to-buy appearance; returns update daily"
      />

      <div className="performance-grid">
        <PerformanceTable
          title="Open positions"
          rows={data.openPositions}
          emptyMessage="No open positions yet."
        />
        <PerformanceTable
          title="Recently closed"
          rows={data.closedPositions}
          emptyMessage="Closed positions will appear here after a ticker drops off the ready list."
        />
      </div>
    </main>
  );
}
