// Backend API address — EDIT THIS FILE ON THE SERVER, no rebuild needed.
//
// Deliberately empty in version control. This repository is public, and a live
// backend URL committed here would publish a known, targetable endpoint next to
// the code that documents exactly what its API does. The real value belongs on
// the host that serves the dashboard, not in git.
//
// Cloud Run service URLs are stable: they only change if the service is
// deleted and recreated, renamed, or moved to another region/project. So this
// normally gets set once, on the server, after the first upload.
//
// No trailing slash. Leave empty if the API is on this same domain.
//   window.UXAGENT_API_BASE = "https://<service>-<hash>.<region>.run.app";
window.UXAGENT_API_BASE = "";
