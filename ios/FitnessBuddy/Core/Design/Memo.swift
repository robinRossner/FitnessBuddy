import FitnessCore
import Foundation

/// Remembers the last result of an expensive transform and recomputes it only when its inputs change. Keep one in
/// `@State` and call it from `body`: a render whose inputs are unchanged (scrolling, a sheet, a timer tick elsewhere)
/// reuses the value. Not observable on purpose: storing the result never triggers another render.
@MainActor
final class Memo<Key: Equatable, Value> {
    private var key: Key?
    private var value: Value?

    func callAsFunction(_ key: Key, _ compute: () -> Value) -> Value {
        if let value, self.key == key { return value }
        let fresh = compute()
        self.key = key
        value = fresh
        return fresh
    }
}

/// What identifies a cached plan row for memoising (a new sync that changes the plan changes the key).
struct PlanRowKey: Equatable {
    var id: String
    var status: String
    var payload: Data

    init(_ row: CachedPlannedSession) {
        id = row.id
        status = row.status
        payload = row.payload
    }
}
