const DEFAULT_FALKORDB_BROWSER_URL = `${window.location.protocol}//${window.location.hostname}:3004`;

const FALKORDB_BROWSER_URL =
  import.meta.env.VITE_FALKORDB_BROWSER_URL || DEFAULT_FALKORDB_BROWSER_URL;

export function GraphDatabasePanel() {
  return (
    <div className="graph-database-panel fade-in">
      <div className="graph-db-toolbar">
        <div>
          <div className="section-title">FalkorDB Browser</div>
          <p className="graph-db-subtitle">
            QueryWeaver-style schema graph store for tables, columns, joins and retrieval paths.
          </p>
        </div>
        <div className="graph-db-actions">
          <a className="btn btn-secondary" href={FALKORDB_BROWSER_URL} target="_blank" rel="noreferrer">
            Open Browser
          </a>
        </div>
      </div>

      <div className="graph-db-connection-hint">
        <span>Manual connection</span>
        <code>Host: localhost</code>
        <code>Port: 6379</code>
        <code>Username: blank</code>
        <code>Password: blank</code>
        <code>TLS: off</code>
      </div>

      <iframe
        className="embed-frame graph-db-frame"
        src={FALKORDB_BROWSER_URL}
        title="FalkorDB Graph Database"
      />
    </div>
  );
}
