import Foundation
import SwiftData

/// Rest-timer times: one default (2:00) and a per-exercise override that sticks.
///
/// The truth lives on the server (`exercise_overrides.<slug>.rest_s`, contract: docs/api/exercises.md); the app
/// keeps a `LocalRestOverride` per variant id so a change applies at once, works offline and is sent with the
/// next sync. A pull reconciles the rows with the server (see `reconcile`).
enum RestTime {
    /// Used when neither the plan nor the synced config says anything.
    static let fallbackSeconds = 120
    /// The picker offers 1:00 to 5:00 in 15 s steps (the server accepts 30...600; a value outside the
    /// range, e.g. from the coach, is still shown and kept as the current choice).
    static let pickerRange = 60...300
    static let pickerStep = 15

    static var pickerValues: [Int] { Array(stride(from: pickerRange.lowerBound, through: pickerRange.upperBound, by: pickerStep)) }

    /// Picker choices for the current value: `nil` is "Default"; a current value outside the list is added.
    static func pickerChoices(current: Int?) -> [Int] {
        var values = pickerValues
        if let current, !values.contains(current) { values.append(current); values.sort() }
        return values
    }

    /// "2:00", "3:15"
    static func format(_ seconds: Int) -> String { "\(seconds / 60):" + String(format: "%02d", seconds % 60) }

    static func defaultLabel(default seconds: Int) -> String { "Default (\(format(seconds)))" }

    /// Resolution order for the rest after a set of this exercise:
    /// 1. the local override row (set here and maybe not sent yet, or the last value reconciled with the server;
    ///    a row without a value means "default"),
    /// 2. the `rest_s` the plan carried,
    /// 3. the server's default from the synced config, else 120 s.
    /// The row comes first because it is at least as new as the plan (a plan cached before the change, or the
    /// plan a running workout started from, would still carry the old value).
    static func resolve(local: LocalRestOverride?, planned: Int?, configDefault: Int?) -> Int {
        let fallback = configDefault ?? fallbackSeconds
        if let local { return local.restS ?? fallback }
        return planned ?? fallback
    }
}

/// Reads and writes `LocalRestOverride` rows.
enum RestSettings {
    static func override(for variantId: Int, in ctx: ModelContext) -> LocalRestOverride? {
        try? ctx.fetch(FetchDescriptor<LocalRestOverride>(predicate: #Predicate { $0.variantId == variantId })).first
    }

    static func configDefault(in ctx: ModelContext) -> Int? {
        SyncState.current(in: ctx).config?.lifting?.restDefaultS
    }

    static func resolve(variantId: Int, planned: Int?, in ctx: ModelContext) -> Int {
        RestTime.resolve(local: override(for: variantId, in: ctx), planned: planned, configDefault: configDefault(in: ctx))
    }

    /// Stores the user's choice (`nil` = back to the default) and flags it for upload.
    @discardableResult
    static func set(variantId: Int, seconds: Int?, in ctx: ModelContext, now: Date = .now) -> LocalRestOverride {
        let row = override(for: variantId, in: ctx) ?? {
            let n = LocalRestOverride(variantId: variantId)
            ctx.insert(n)
            return n
        }()
        row.restS = seconds
        row.pending = true
        row.updatedAt = now
        try? ctx.save()
        return row
    }

    static func pending(in ctx: ModelContext) -> [LocalRestOverride] {
        let rows = (try? ctx.fetch(FetchDescriptor<LocalRestOverride>(predicate: #Predicate { $0.pending },
                                                                       sortBy: [SortDescriptor(\.updatedAt)]))) ?? []
        return rows
    }

    /// The server accepted `stamp`'s value: stop being pending unless the user changed it again meanwhile.
    /// The server's answer is the truth for the stored value (`nil` when the exercise is back on the default).
    static func markSent(variantId: Int, stamp: Date, answer: RestResponseDTO?, in ctx: ModelContext) {
        guard let row = override(for: variantId, in: ctx), row.updatedAt == stamp else { return }
        row.pending = false
        if let answer { row.restS = answer.isDefault == true ? nil : answer.restS }
        try? ctx.save()
    }

    /// Pull: line the rows up with the server's overrides (`config.lifting.rest_s_by_variant`).
    /// Rows that still wait for an upload are left alone (the user's latest intent wins and is sent first).
    /// No-op against an older server that does not send the block.
    static func reconcile(_ lifting: LiftingConfigDTO?, in ctx: ModelContext) {
        guard let byVariant = lifting?.restSByVariant else { return }
        var server: [Int: Int] = [:]
        for (key, value) in byVariant { if let id = Int(key) { server[id] = value } }
        let rows = (try? ctx.fetch(FetchDescriptor<LocalRestOverride>())) ?? []
        for row in rows where !row.pending { row.restS = server[row.variantId] }
        let known = Set(rows.map(\.variantId))
        for (id, seconds) in server where !known.contains(id) {
            let row = LocalRestOverride(variantId: id, restS: seconds)
            ctx.insert(row)
        }
    }
}

/// Sends pending overrides to `PUT /exercises/{id}/rest`. Transient failures (offline, 5xx) throw so the sync
/// fails and retries with its backoff; the row stays pending. A request the server rejects for good (unknown
/// variant, invalid value) is dropped from the queue so it cannot block the sync: the next pull restores the
/// server's value.
enum RestSync {
    @discardableResult
    static func push(using client: APIClient, container: ModelContainer) async throws -> Int {
        let pending = RestSettings.pending(in: ModelContext(container)).map { (variantId: $0.variantId, restS: $0.restS, stamp: $0.updatedAt) }
        var sent = 0
        for item in pending {
            try Task.checkCancellation()
            do {
                let answer = try await client.setRest(variantId: item.variantId, restS: item.restS)
                RestSettings.markSent(variantId: item.variantId, stamp: item.stamp, answer: answer, in: ModelContext(container))
                sent += 1
            } catch let error as APIError {
                switch error {
                case .http(404, _), .http(422, _):
                    RestSettings.markSent(variantId: item.variantId, stamp: item.stamp, answer: nil, in: ModelContext(container))
                default:
                    throw error
                }
            }
        }
        return sent
    }
}
