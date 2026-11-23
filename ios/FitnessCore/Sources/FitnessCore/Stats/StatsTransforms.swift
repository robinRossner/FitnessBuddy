import Foundation

/// `YYYY-MM-DD` <-> `Date` (start of that day in the given calendar). Charts plot on `Date`, the API speaks strings.
public enum DayKey: Sendable {
    public static func date(_ s: String, calendar: Calendar = .current) -> Date? {
        let parts = s.prefix(10).split(separator: "-")
        guard parts.count == 3, let y = Int(parts[0]), let m = Int(parts[1]), let d = Int(parts[2]) else { return nil }
        return calendar.date(from: DateComponents(year: y, month: m, day: d))
    }

    public static func string(_ date: Date, calendar: Calendar = .current) -> String {
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    /// Monday of the week containing `date` (the backend's `week_start`).
    public static func weekStart(_ date: Date, calendar: Calendar = .current) -> String {
        var cal = calendar
        cal.firstWeekday = 2
        let start = cal.dateInterval(of: .weekOfYear, for: date)?.start ?? date
        return string(start, calendar: cal)
    }

    /// Calendar-correct "n days ago" (start of day).
    public static func daysAgo(_ n: Int, from now: Date, calendar: Calendar = .current) -> Date {
        calendar.startOfDay(for: calendar.date(byAdding: .day, value: -n, to: now) ?? now)
    }
}

// MARK: - Lifts

public enum LiftRange: String, CaseIterable, Identifiable, Sendable {
    case threeMonths = "3M", sixMonths = "6M", year = "1Y", all = "All"
    public var id: String { rawValue }

    public func cutoff(now: Date = AppClock.instant, calendar: Calendar = .current) -> Date? {
        let months: Int
        switch self {
        case .threeMonths: months = 3
        case .sixMonths: months = 6
        case .year: months = 12
        case .all: return nil
        }
        return calendar.date(byAdding: .month, value: -months, to: calendar.startOfDay(for: now))
    }

    /// Points on or after the cutoff (all points for `.all`), sorted by date.
    public func filter(_ points: [LiftPointDTO], now: Date = AppClock.instant, calendar: Calendar = .current) -> [LiftPointDTO] {
        let sorted = points.sorted { $0.date < $1.date }
        guard let cutoff = cutoff(now: now, calendar: calendar) else { return sorted }
        let key = DayKey.string(cutoff, calendar: calendar)
        return sorted.filter { $0.date >= key }
    }
}

public enum Trend: Sendable {
    case up, flat, down, unknown

    /// ±1 % is "flat".
    public init(pct: Double?) {
        guard let pct else { self = .unknown; return }
        self = pct > 1 ? .up : (pct < -1 ? .down : .flat)
    }

    public var symbol: String {
        switch self {
        case .up: "arrow.up.right"
        case .down: "arrow.down.right"
        case .flat: "arrow.right"
        case .unknown: "minus"
        }
    }
}

public enum LiftListing: Sendable {
    /// Active lifts first, then by name. Search matches name, gym and muscle group; `muscle` filters exactly.
    public static func filtered(_ lifts: [LiftSummaryDTO], query: String, muscle: String?) -> [LiftSummaryDTO] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return lifts.filter { lift in
            if let muscle, MuscleGroup.merged(lift.muscleGroup) != muscle { return false }
            guard !q.isEmpty else { return true }
            return [lift.name, lift.gymName ?? "", MuscleGroup.display(MuscleGroup.merged(lift.muscleGroup))].contains { $0.lowercased().contains(q) }
        }
        .sorted { a, b in
            if a.isActive != b.isActive { return a.isActive }
            return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
        }
    }

    public static func muscleGroups(_ lifts: [LiftSummaryDTO]) -> [String] {
        Array(Set(lifts.compactMap { MuscleGroup.merged($0.muscleGroup) })).sorted()
    }

    /// PRs in the window = lifts whose latest PR date falls inside it.
    public static func recentPRCount(_ lifts: [LiftSummaryDTO], days: Int = 30, now: Date = AppClock.instant, calendar: Calendar = .current) -> Int {
        let key = DayKey.string(DayKey.daysAgo(days, from: now, calendar: calendar), calendar: calendar)
        return lifts.filter { ($0.lastPrDate ?? "") >= key }.count
    }
}

public enum MuscleGroup: Sendable {
    /// Rear delts are part of shoulders now; cached data and old servers may still say `rear_delts`.
    public static func merged(_ raw: String?) -> String? {
        raw == "rear_delts" ? "shoulders" : raw
    }


    /// "upper_back" / "upperBack" -> "Upper back". (Dictionary keys may arrive camel-cased from the snake-case decoder.)
    public static func display(_ raw: String?) -> String {
        guard let raw, !raw.isEmpty else { return "Other" }
        var out = ""
        for ch in raw {
            if ch == "_" { out += " " } else if ch.isUppercase { out += " " + ch.lowercased() } else { out.append(ch) }
        }
        return out.prefix(1).uppercased() + out.dropFirst()
    }
}

public struct WeekSummary: Equatable, Sendable {
    public var sets: Int
    public var volumeKg: Double
}

public enum VolumeTransforms: Sendable {
    /// This week's lifting numbers (zero when the newest week is an older one).
    public static func thisWeek(_ weeks: [WeekVolumeDTO], now: Date = AppClock.instant, calendar: Calendar = .current) -> WeekSummary {
        let key = DayKey.weekStart(now, calendar: calendar)
        guard let week = weeks.first(where: { $0.weekStart == key }) else { return WeekSummary(sets: 0, volumeKg: 0) }
        return WeekSummary(sets: week.liftingSets ?? 0, volumeKg: week.liftingVolumeKg ?? 0)
    }

    /// Sets per muscle group over the last `weeks` weeks, biggest first.
    public static func setsByMuscle(_ weeks: [WeekVolumeDTO], last: Int = 4) -> [(group: String, sets: Int)] {
        var totals: [String: Int] = [:]
        for w in weeks.sorted(by: { $0.weekStart < $1.weekStart }).suffix(last) {
            for (k, v) in w.setsByMuscleGroup ?? [:] { totals[MuscleGroup.merged(k) ?? k, default: 0] += v }
        }
        return totals.map { ($0.key, $0.value) }.sorted { $0.sets > $1.sets }
    }
}

// MARK: - Running

public struct ZoneSegment: Equatable, Identifiable, Sendable {
    public var weekStart: String
    public var zone: Int  // 1...5
    public var minutes: Double
    public var id: String { "\(weekStart)-z\(zone)" }
    public var name: String { "Z\(zone)" }
}

public enum RunTransforms: Sendable {
    public static let zoneNames = ["Z1", "Z2", "Z3", "Z4", "Z5"]

    /// Five segments per week in zone order Z1 (cool, bottom of the stack) ... Z5 (hot, top). Missing zones are 0.
    /// Weeks without any zone data are skipped, so the chart does not claim "0 minutes" for unrecorded runs.
    public static func zoneSegments(_ weeks: [RunWeekDTO]) -> [ZoneSegment] {
        weeks.sorted { $0.weekStart < $1.weekStart }.flatMap { week -> [ZoneSegment] in
            let zones = week.timeInZonesS ?? [:]
            guard zones.values.contains(where: { $0 > 0 }) else { return [] }
            return (1...5).map { z in
                ZoneSegment(weekStart: week.weekStart, zone: z, minutes: (zones["z\(z)"] ?? zones["Z\(z)"] ?? 0) / 60)
            }
        }
    }

    /// Weeks to chart: sorted, and trimmed of empty leading weeks (the Garmin data starts 2026-08-17).
    public static func weeksWithRuns(_ weeks: [RunWeekDTO]) -> [RunWeekDTO] {
        let sorted = weeks.sorted { $0.weekStart < $1.weekStart }
        guard let first = sorted.firstIndex(where: { ($0.km ?? 0) > 0 || ($0.runs ?? 0) > 0 }) else { return [] }
        return Array(sorted[first...])
    }

    public static func nearest<T>(_ items: [T], to date: Date, key: (T) -> String) -> T? {
        items.min { a, b in
            abs((DayKey.date(key(a)) ?? .distantPast).timeIntervalSince(date)) < abs((DayKey.date(key(b)) ?? .distantPast).timeIntervalSince(date))
        }
    }
}

// MARK: - Health

public enum HealthRange: Int, CaseIterable, Identifiable, Sendable {
    case thirty = 30, ninety = 90
    public var id: Int { rawValue }
    public var label: String { "\(rawValue) days" }
}

public enum HealthTransforms: Sendable {
    public static func sleepHours(_ seconds: Double?) -> Double? { seconds.map { $0 / 3600 } }

    /// Days inside the window (today and the `range - 1` days before), sorted.
    public static func window(_ days: [HealthDayDTO], range: HealthRange, now: Date = AppClock.instant, calendar: Calendar = .current) -> [HealthDayDTO] {
        let key = DayKey.string(DayKey.daysAgo(range.rawValue - 1, from: now, calendar: calendar), calendar: calendar)
        return days.filter { $0.date >= key }.sorted { $0.date < $1.date }
    }

    public static func latestReadinessDay(_ days: [HealthDayDTO]) -> HealthDayDTO? {
        days.sorted { $0.date < $1.date }.last { $0.readiness != nil }
    }

    /// Percent change 7-day vs 28-day average (nil when either is missing / zero).
    public static func deltaPct(short: Double?, long: Double?) -> Double? {
        guard let short, let long, long != 0 else { return nil }
        return (short - long) / long * 100
    }
}

// MARK: - Bodyweight

/// A body measurement logged on (or synced to) this device, reduced to what the chart needs.
public struct LocalBodyweight: Equatable, Sendable, Identifiable {
    public var id: UUID
    public var measuredAt: Date
    public var weightKg: Double
    public var deleted: Bool
    /// Not pushed yet.
    public var pending: Bool

    public init(id: UUID, measuredAt: Date, weightKg: Double, deleted: Bool, pending: Bool) {
        self.id = id
        self.measuredAt = measuredAt
        self.weightKg = weightKg
        self.deleted = deleted
        self.pending = pending
    }
}

public struct BodyweightEntry: Equatable, Identifiable, Sendable {
    public var id: String
    public var day: String
    public var weightKg: Double
    public var source: String?
    public var isPending: Bool
    public var date: Date { DayKey.date(day) ?? .distantPast }
}

public enum BodyweightMerge: Sendable {
    /// One point per day, ascending. A local entry (pending or already synced) wins over the server value of the same day,
    /// so a weight logged offline shows up at once and does not appear twice once the stats catch up. A day whose only
    /// local entry was deleted hides the matching server value (unless it came from the watch scale, which we cannot delete).
    public static func merge(server: [BodyweightPointDTO], local: [LocalBodyweight], calendar: Calendar = .current) -> [BodyweightEntry] {
        var live: [String: LocalBodyweight] = [:]
        var tombstoneDays = Set<String>()
        for l in local.sorted(by: { $0.measuredAt < $1.measuredAt }) {
            let day = DayKey.string(l.measuredAt, calendar: calendar)
            if l.deleted { tombstoneDays.insert(day) } else { live[day] = l }
        }
        var byDay: [String: BodyweightEntry] = [:]
        for s in server {
            let day = String(s.date.prefix(10))
            if live[day] != nil { continue }
            if tombstoneDays.contains(day), (s.source ?? "").lowercased() != "garmin" { continue }
            byDay[day] = BodyweightEntry(id: "srv-\(day)", day: day, weightKg: s.weightKg, source: s.source, isPending: false)
        }
        for (day, l) in live {
            byDay[day] = BodyweightEntry(id: l.id.uuidString.lowercased(), day: day, weightKg: l.weightKg, source: "you", isPending: l.pending)
        }
        return byDay.values.sorted { $0.day < $1.day }
    }

    /// Change vs. the entry closest to 28 days before the latest one (within ±7 days); nil if there is none.
    public static func change4wPct(_ entries: [BodyweightEntry], calendar: Calendar = .current) -> Double? {
        guard let latest = entries.last, let latestDate = DayKey.date(latest.day, calendar: calendar),
              let target = calendar.date(byAdding: .day, value: -28, to: latestDate) else { return nil }
        let candidates = entries.dropLast().compactMap { e -> (BodyweightEntry, Double)? in
            guard let d = DayKey.date(e.day, calendar: calendar) else { return nil }
            let off = abs(d.timeIntervalSince(target)) / 86_400
            return off <= 7 ? (e, off) : nil
        }
        guard let ref = candidates.min(by: { $0.1 < $1.1 })?.0, ref.weightKg > 0 else { return nil }
        return (latest.weightKg - ref.weightKg) / ref.weightKg * 100
    }

    public static func within(_ entries: [BodyweightEntry], days: Int, now: Date = AppClock.instant, calendar: Calendar = .current) -> [BodyweightEntry] {
        let key = DayKey.string(DayKey.daysAgo(days - 1, from: now, calendar: calendar), calendar: calendar)
        return entries.filter { $0.day >= key }
    }
}
