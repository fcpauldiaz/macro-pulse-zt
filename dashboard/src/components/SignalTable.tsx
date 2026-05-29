import type { DailySignal } from "@/lib/types";

function formatMoney(value: number | null): string {
  if (value === null) {
    return "—";
  }
  return `$${value.toFixed(2)}`;
}

function formatNumber(value: number | null, digits = 2): string {
  if (value === null) {
    return "—";
  }
  return value.toFixed(digits);
}

type SignalTableProps = {
  title: string;
  subtitle: string;
  accentClass: string;
  rows: DailySignal[];
  mode: "momentum" | "quant";
};

export function SignalTable({
  title,
  subtitle,
  accentClass,
  rows,
  mode,
}: SignalTableProps) {
  return (
    <section className={`panel signal-panel ${accentClass}`}>
      <div className="signal-panel-header">
        <div>
          <p className="signal-kicker">{subtitle}</p>
          <h3 className="signal-title">{title}</h3>
        </div>
        <span className="signal-count">{rows.length} tickers</span>
      </div>

      {rows.length === 0 ? (
        <div className="empty-state compact">No ready-to-buy signals for this snapshot.</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>ETF</th>
                <th>Price</th>
                {mode === "momentum" ? <th>SMA50</th> : <th>Quant</th>}
                <th>Beta</th>
                {mode === "quant" ? <th>RSI</th> : <th>Signal</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.symbol}>
                  <td>
                    <div className="ticker-cell">
                      <span className="ticker-symbol">{row.symbol}</span>
                      {row.industry ? (
                        <span className="ticker-meta">{row.industry}</span>
                      ) : null}
                    </div>
                  </td>
                  <td>
                    <span className="etf-pill">{row.etf ?? "—"}</span>
                  </td>
                  <td className="mono">{formatMoney(row.price)}</td>
                  <td className="mono">
                    {mode === "momentum"
                      ? formatMoney(row.sma50)
                      : formatNumber(row.quantScore)}
                  </td>
                  <td className="mono">{formatNumber(row.beta)}</td>
                  <td className="mono">
                    {mode === "quant"
                      ? formatNumber(row.rsi, 1)
                      : (row.signalLabel ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
