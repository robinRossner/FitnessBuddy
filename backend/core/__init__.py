"""Code shared by every app served from this backend (fitness, later receipts).

Only app-independent plumbing lives here: bearer auth, the shared settings (paths, API token,
scheduler switches, Claude credentials) and scheduler helpers. Core never imports from `apps`.
"""
