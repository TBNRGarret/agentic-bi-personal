type Severity = "critical" | "warning" | "info";

interface AlertRule {
  id: string;
  name: string;
  domain: string;
  severity: Severity;
  condition: string;
  window: string;
  action: string;
}

const ALERT_RULES: AlertRule[] = [
  {
    id: "llm-cost-budget",
    name: "LLM daily budget exceeded",
    domain: "Cost",
    severity: "critical",
    condition: "Total LLM cost > $10/day; warn at $5/day",
    window: "Daily",
    action: "Identify the model and agent responsible, then apply a budget or routing limit.",
  },
  {
    id: "llm-cost-anomaly",
    name: "Unexpected model cost spike",
    domain: "Cost",
    severity: "warning",
    condition: "Hourly cost > 2x the 7-day baseline and absolute increase > $1",
    window: "15 min evaluation",
    action: "Compare model, user and agent traffic with the previous period.",
  },
  {
    id: "agent-error-rate",
    name: "Agent query failure rate",
    domain: "Reliability",
    severity: "critical",
    condition: "Failed agent queries > 10%; warn above 5%",
    window: "15 min",
    action: "Inspect SQL correction attempts, provider errors and affected data sources.",
  },
  {
    id: "latency-p95",
    name: "High agent response latency",
    domain: "Performance",
    severity: "warning",
    condition: "p95 response latency > 5s; warn above 2s",
    window: "10 min",
    action: "Separate LLM, metadata retrieval and database execution latency.",
  },
  {
    id: "freshness-gold",
    name: "Gold dataset is stale",
    domain: "Freshness",
    severity: "critical",
    condition: "No Gold update for 6h; warn after 2h",
    window: "Every 15 min",
    action: "Check server ingestion, the current DAG run and the last successful transformation.",
  },
  {
    id: "ingestion-failure",
    name: "Ingestion batch failed or quarantined",
    domain: "Ingestion",
    severity: "critical",
    condition: "Any failed batch or quarantined row count > 0",
    window: "Per batch",
    action: "Stop downstream promotion for the batch and inspect rejected records.",
  },
  {
    id: "server-quality",
    name: "Server quality check failed",
    domain: "Data Quality",
    severity: "critical",
    condition: "Any error-severity quality gate fails; warn-severity failures > 3",
    window: "Per server quality run",
    action: "Mark affected models degraded and link the failed test and table.",
  },
  {
    id: "telemetry-gap",
    name: "Telemetry collection gap",
    domain: "Coverage",
    severity: "warning",
    condition: "No traces or LLM generations for an active source for 30 min",
    window: "5 min evaluation",
    action: "Verify collectors, credentials, source connectivity and clock skew.",
  },
  {
    id: "token-anomaly",
    name: "Token usage anomaly",
    domain: "Usage",
    severity: "warning",
    condition: "Tokens/request or total tokens > 3x the 7-day baseline",
    window: "30 min",
    action: "Find prompt growth, retry loops and unexpectedly verbose completions.",
  },
];

export function AlertsPanel() {
  const critical = ALERT_RULES.filter((rule) => rule.severity === "critical").length;
  const warning = ALERT_RULES.filter((rule) => rule.severity === "warning").length;

  return (
    <div className="alerts-page fade-in">
      <div className="alerts-summary-grid">
        <div className="alerts-summary-card">
          <span>Default rules</span>
          <strong>{ALERT_RULES.length}</strong>
          <small>Enabled rule templates</small>
        </div>
        <div className="alerts-summary-card critical">
          <span>Critical</span>
          <strong>{critical}</strong>
          <small>Immediate investigation</small>
        </div>
        <div className="alerts-summary-card warning">
          <span>Warning</span>
          <strong>{warning}</strong>
          <small>Early operational signal</small>
        </div>
      </div>

      <div className="alerts-rule-grid">
        {ALERT_RULES.map((rule) => (
          <article key={rule.id} className={`alert-rule-card ${rule.severity}`}>
            <div className="alert-rule-header">
              <div>
                <span className="alert-rule-domain">{rule.domain}</span>
                <h3>{rule.name}</h3>
              </div>
              <span className={`alert-severity ${rule.severity}`}>{rule.severity}</span>
            </div>
            <dl className="alert-rule-details">
              <div><dt>Trigger</dt><dd>{rule.condition}</dd></div>
              <div><dt>Window</dt><dd>{rule.window}</dd></div>
              <div><dt>Response</dt><dd>{rule.action}</dd></div>
            </dl>
            <div className="alert-rule-footer">
              <span className="status-dot up" />
              Enabled by default
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
