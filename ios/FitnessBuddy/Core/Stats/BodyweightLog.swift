import FitnessCore
import Foundation
import SwiftData

/// Local create / delete of body measurements. Goes through the normal outbox path: the record is dirty, the next sync
/// pushes it (works offline), a delete is a tombstone (`deletedAt`) that is pushed the same way.
enum BodyweightLog {
    /// Weight range the sheet accepts.
    static let validRange: ClosedRange<Double> = 20...400

    static func isValid(_ kg: Double) -> Bool { validRange.contains(kg) }

    /// Parses "78,4" / "78.4" / " 78.4 kg ".
    static func parse(_ text: String) -> Double? {
        let cleaned = text.lowercased().replacingOccurrences(of: "kg", with: "")
            .replacingOccurrences(of: ",", with: ".").trimmingCharacters(in: .whitespaces)
        return Double(cleaned)
    }

    /// Today keeps the current time; another day is logged at noon local time so its calendar day is unambiguous.
    static func measuredAt(for day: Date, now: Date = .now, calendar: Calendar = .current) -> Date {
        if calendar.isDate(day, inSameDayAs: now) { return now }
        return calendar.date(bySettingHour: 12, minute: 0, second: 0, of: day) ?? day
    }

    @discardableResult
    @MainActor
    static func add(weightKg: Double, day: Date, note: String? = nil, in ctx: ModelContext, now: Date = .now) -> LocalBodyMeasurement {
        let m = LocalBodyMeasurement(measuredAt: measuredAt(for: day, now: now), weightKg: (weightKg * 10).rounded() / 10,
                                     note: note, updatedAt: now)
        ctx.insert(m)
        try? ctx.save()
        return m
    }

    @MainActor
    static func delete(_ m: LocalBodyMeasurement, in ctx: ModelContext, now: Date = .now) {
        m.deletedAt = now
        m.touch(now)
        try? ctx.save()
    }

    static func snapshot(_ m: LocalBodyMeasurement) -> LocalBodyweight {
        LocalBodyweight(id: m.id, measuredAt: m.measuredAt, weightKg: m.weightKg, deleted: m.deletedAt != nil,
                        pending: m.dirty && m.rejectedReason == nil)
    }
}
