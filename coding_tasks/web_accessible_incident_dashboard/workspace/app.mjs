export function filterIncidents(_incidents, _filters) {
  return [];
}

export function summarizeIncidents(_incidents) {
  return { open: 0, monitoring: 0, resolved: 0, total: 0 };
}

export function normalizeFilters(_searchParams) {
  return { query: "", status: "all" };
}

// TODO: load incidents and implement the browser application.
