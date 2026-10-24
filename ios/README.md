# FitnessBuddy iOS (placeholder)

SwiftUI app – a later step. Plan:

- Log strength sets offline; sets/sessions get client-generated UUIDs so sync to the backend
  is idempotent (upsert by id, tombstones via `deleted_at`).
- Show today's planned session (approval mode: manual) and readiness.
- Optional HealthKit fallback as a `GarminSource` implementation.
