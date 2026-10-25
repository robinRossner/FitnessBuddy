import CryptoKit
import Foundation
import SwiftData

/// Everything cached locally belongs to exactly one backend (URL + token). When that changes, or the user asks
/// for a reset, the synced / cached data is wiped, the cursor is reset and the next sync is a full snapshot.
/// Real, unsynced local workouts are the only thing that survives.
enum BackendReset {
    /// Stable identifier of "this backend with this token"; nil while not configured.
    /// Only a hash is stored (no token in the database).
    static func fingerprint(url: String, token: String) -> String? {
        let token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let base = APIClient.normalizedBaseURL(url), !token.isEmpty else { return nil }
        let digest = SHA256.hash(data: Data("\(base.absoluteString.lowercased())\n\(token)".utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    /// True when the stored cache belongs to a different backend than `fingerprint`, or holds demo data.
    static func needsReset(fingerprint: String?, in ctx: ModelContext) -> Bool {
        let state = SyncState.current(in: ctx)
        if state.demoActive { return true }
        return state.backendFingerprint != fingerprint
    }

    /// Real (non-demo) records that were never synced and would survive a reset.
    static func unsyncedCount(in ctx: ModelContext) -> Int {
        let sessions = (try? ctx.fetch(FetchDescriptor<LocalSession>(predicate: #Predicate { $0.dirty && !$0.isDemo }))) ?? []
        let sets = (try? ctx.fetch(FetchDescriptor<LocalSet>(predicate: #Predicate { $0.dirty && !$0.isDemo }))) ?? []
        let bodies = (try? ctx.fetch(FetchDescriptor<LocalBodyMeasurement>(predicate: #Predicate { $0.dirty && !$0.isDemo }))) ?? []
        // an untouched, not-started workout skeleton is not worth a warning
        let realSets = sets.filter { $0.completedAt != nil || $0.deletedAt != nil || $0.hasBeenPushed }
        let setSessionIds = Set(realSets.map(\.sessionId))
        let realSessions = sessions.filter {
            $0.endedAt != nil || $0.deletedAt != nil || $0.hasBeenPushed || setSessionIds.contains($0.id)
        }
        return realSessions.count + realSets.count + bodies.count
    }

    /// Wipes demo rows, clean synced rows and all caches; resets cursor / status / demo flag and stores
    /// `fingerprint`. Dirty real records are kept (and re-armed for the new backend). Returns how many
    /// records were kept.
    @discardableResult
    static func reset(in ctx: ModelContext, fingerprint: String?) -> Int {
        let sessions = (try? ctx.fetch(FetchDescriptor<LocalSession>())) ?? []
        let sets = (try? ctx.fetch(FetchDescriptor<LocalSet>())) ?? []
        let bodies = (try? ctx.fetch(FetchDescriptor<LocalBodyMeasurement>())) ?? []

        let keptSessions = sessions.filter { $0.dirty && !$0.isDemo }
        let keptSessionIds = Set(keptSessions.map(\.id))
        var kept = 0

        for s in sessions {
            if s.dirty && !s.isDemo { rearm(s); kept += 1 } else { ctx.delete(s) }
        }
        for s in sets {
            // all real sets of a kept workout travel with it
            if !s.isDemo && (s.dirty || keptSessionIds.contains(s.sessionId)) { rearm(s); kept += 1 } else { ctx.delete(s) }
        }
        for b in bodies {
            if b.dirty && !b.isDemo { rearm(b); kept += 1 } else { ctx.delete(b) }
        }
        try? ctx.delete(model: CachedPlannedSession.self)
        try? ctx.delete(model: CachedVariant.self)
        try? ctx.delete(model: CachedExercise.self)
        try? ctx.delete(model: CachedGym.self)

        let state = SyncState.current(in: ctx)
        state.cursor = nil
        state.readinessData = nil
        state.readinessDate = nil
        state.readinessTodayData = nil
        state.configData = nil
        state.coachPendingCount = 0
        state.coachLatestPendingId = nil
        state.lastSyncAt = nil
        state.lastError = nil
        state.lastAttemptAt = nil
        state.lastSummary = nil
        state.demoActive = false
        state.backendFingerprint = fingerprint
        UserDefaults.standard.removeObject(forKey: AppClock.overrideKey)
        try? ctx.save()
        return kept
    }

    // The new server has never seen these: treat them as brand new.
    private static func rearm(_ s: LocalSession) { s.dirty = true; s.hasBeenPushed = false; s.rejectedReason = nil }
    private static func rearm(_ s: LocalSet) { s.dirty = true; s.hasBeenPushed = false; s.rejectedReason = nil }
    private static func rearm(_ b: LocalBodyMeasurement) { b.dirty = true; b.hasBeenPushed = false; b.rejectedReason = nil }
}
