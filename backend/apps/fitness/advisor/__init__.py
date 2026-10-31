"""Claude advisor (step 6; contract: docs/api/coach.md).

Consulted rarely (open engine triggers, a Sunday weekly review, the user's "Ask coach"). Claude
only proposes parameter changes; `validator` decides what is allowed and how risky it is,
`service` stores and applies them (new config version, author advisor), `outcomes` judges them
21-28 days later.
"""
