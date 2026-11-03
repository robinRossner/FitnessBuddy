import Foundation

/// Pure logic of the Summary tab: pinned metrics, highlights and the next lifting session. Only real data: a highlight
/// whose inputs are missing is left out instead of being guessed.
public enum SummaryLogic: Sendable {
    // MARK: Pinned

    public static let pinnedKey = "summary.pinned"
    public static let defaultPinnedRaw = MetricCatalog.defaultPinned.joined(separator: ",")

    /// Known metric ids in order, duplicates dropped.
    public static func pinnedIds(_ raw: String) -> [String] {
        var seen = Set<String>()
        return raw.split(separator: ",").map(String.init)
            .filter { MetricCatalog.info($0) != nil && seen.insert($0).inserted }
    }

    public static func raw(_ ids: [String]) -> String { ids.joined(separator: ",") }

    // MARK: Highlights

    public struct Highlight: Identifiable, Equatable, Sendable {
        public enum Target: Equatable, Sendable {
            /// A MetricCatalog id: opens the metric detail chart.
            case metric(String)
            case lift(LiftSummaryDTO)
        }
        public var id: String
        public var title: String
        public var symbol: String
        public var domain: Domain
        public var sentence: String
        public var bars: [HighlightBar]
        public var target: Target
    }

    public static func highlights(volume: [WeekVolumeDTO], health: HealthSummaryDTO?, lifts: [LiftSummaryDTO],
                           now: Date = AppClock.instant, calendar: Calendar = .current) -> [Highlight] {
        [lifting(lifts, now: now, calendar: calendar), running(volume, now: now, calendar: calendar), sleep(health)]
            .compactMap { $0 }
    }

    /// Two bars, the longer one full width.
    public static func bars(current: Double, currentLabel: String, reference: Double, referenceLabel: String,
                     unit: String, decimals: Int) -> [HighlightBar] {
        let top = max(current, reference, 0.0001)
        func text(_ v: Double) -> String { v.formatted(.number.precision(.fractionLength(decimals))) }
        return [
            HighlightBar(value: text(current), unit: unit, label: currentLabel, fraction: current / top, highlighted: true),
            HighlightBar(value: text(reference), unit: unit, label: referenceLabel, fraction: reference / top, highlighted: false),
        ]
    }

    /// Last full week's km vs the average of the 8 weeks before it (needs at least 4 of those weeks).
    public static func running(_ weeks: [WeekVolumeDTO], now: Date = AppClock.instant, calendar: Calendar = .current) -> Highlight? {
        var km: [String: Double] = [:]
        for w in weeks { km[String(w.weekStart.prefix(10))] = w.runningKm ?? 0 }
        func week(_ ago: Int) -> String { DayKey.weekStart(DayKey.daysAgo(7 * ago, from: now, calendar: calendar), calendar: calendar) }
        guard let last = km[week(1)] else { return nil }
        let previous = (2...9).compactMap { km[week($0)] }
        guard previous.count >= 4 else { return nil }
        let avg = previous.reduce(0, +) / Double(previous.count)
        guard avg > 0 else { return nil }
        let change = (last - avg) / avg
        let sentence = abs(change) < 0.05 ? "You ran about your usual distance last week."
            : (change > 0 ? "You ran more last week than your 8-week average." : "You ran less last week than your 8-week average.")
        return Highlight(id: "running", title: "Running", symbol: Domain.running.symbol, domain: .running, sentence: sentence,
                         bars: bars(current: last, currentLabel: "Last week", reference: avg, referenceLabel: "8-week average",
                                    unit: "km", decimals: 1),
                         target: .metric(MetricCatalog.runningDistance.id))
    }

    /// 7-day vs 28-day sleep average from the health summary.
    public static func sleep(_ summary: HealthSummaryDTO?) -> Highlight? {
        guard let s7 = HealthTransforms.sleepHours(summary?.sleep7dS), let s28 = HealthTransforms.sleepHours(summary?.sleep28dS),
              s7 > 0, s28 > 0 else { return nil }
        let change = (s7 - s28) / s28
        let sentence = abs(change) < 0.03 ? "Your sleep in the last 7 days is about your 28-day average."
            : (change > 0 ? "You slept more in the last 7 days than your 28-day average."
                          : "You slept less in the last 7 days than your 28-day average.")
        return Highlight(id: "sleep", title: "Sleep", symbol: Domain.sleep.symbol, domain: .sleep, sentence: sentence,
                         bars: bars(current: s7, currentLabel: "Last 7 days", reference: s28, referenceLabel: "28-day average",
                                    unit: "h", decimals: 1),
                         target: .metric(MetricCatalog.sleep.id))
    }

    /// The program lift with the biggest 4-week e1RM gain (not stalled, at least +0.5 %).
    public static func lifting(_ lifts: [LiftSummaryDTO], now: Date = AppClock.instant, calendar: Calendar = .current) -> Highlight? {
        let candidates = LiftProgram.inProgram(lifts, now: now, calendar: calendar).filter {
            !$0.isStalled && ($0.trend4wPct ?? 0) >= 0.5 && ($0.latestE1rm ?? 0) > 0
        }
        guard let lift = candidates.max(by: { ($0.trend4wPct ?? 0) < ($1.trend4wPct ?? 0) }),
              let latest = lift.latestE1rm, let pct = lift.trend4wPct else { return nil }
        let before = latest / (1 + pct / 100)
        let name = lift.name.withoutGymSuffix
        let gain = Fmt.number(latest - before, maxFraction: 1)
        return Highlight(id: "lifting", title: "Lifting", symbol: Domain.lifting.symbol, domain: .lifting,
                         sentence: "Your \(name) is up \(gain) kg (estimated 1RM) over the last 4 weeks.",
                         bars: bars(current: latest, currentLabel: "Now", reference: before, referenceLabel: "4 weeks ago",
                                    unit: "kg", decimals: 1),
                         target: .lift(lift))
    }

    // MARK: Next lift

    /// The planned lifting session the Workout chooser would preselect (soonest open one from today, else the latest
    /// missed one this week), with "Today" / "Thu" / "Missed Mon" for the card header.
    public static func nextLift(planned: [PlannedLiftRef], today: String, locale: Locale = .current,
                         calendar: Calendar = AppClock.calendar) -> (id: String, when: String, subtitle: String)? {
        let choices = WorkoutChooserLogic.choices(planned: planned, finished: [], today: today, locale: locale, calendar: calendar)
        guard let first = choices.first, case .planned(let id) = first.source,
              let ref = planned.first(where: { $0.id == id }) else { return nil }
        let word = WorkoutChooserLogic.dayWord(ref.date, today: today, locale: locale, calendar: calendar)
        let when = ref.date < today ? "Missed \(word)" : (word == "today" ? "Today" : word)
        let subtitle = ref.exerciseCount > 0 ? "\(ref.exerciseCount) exercises" : ""
        return (id, when, subtitle)
    }
}
