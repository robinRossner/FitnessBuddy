"""App-independent plumbing of the backend: bearer auth, the shared settings (paths, API token,
scheduler switches, Claude credentials), db helpers, log redaction and scheduler helpers. Core never
imports from `apps`.
"""
