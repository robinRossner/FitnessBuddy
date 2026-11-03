import Foundation

/// What the short banner after "Sync" says. Built by `GarminSyncLogic`; the view only draws it.
public struct GarminSyncFeedback: Equatable, Sendable {
    public enum Tone: Equatable, Sendable { case success, neutral, warning }

    public var tone: Tone
    public var title: String
    public var detail: String? = nil
    /// Shows the "Open Garmin Connect" action (nothing new at Garmin yet: the watch may not have uploaded).
    public var offersConnect = false

    public init(tone: Tone, title: String, detail: String? = nil, offersConnect: Bool = false) {
        self.tone = tone
        self.title = title
        self.detail = detail
        self.offersConnect = offersConnect
    }
}

/// Pure mapping from the backend's answer (or an error) to feedback. Copy never claims the watch was synced:
/// we only read Garmin's cloud, the watch uploads on its own through the Garmin Connect app.
public enum GarminSyncLogic: Sendable {
    public static func feedback(for result: GarminSyncResultDTO, today: String = AppClock.todayString) -> GarminSyncFeedback {
        switch result.status {
        case .synced:
            let count = result.newActivities ?? 0
            let name = result.latestActivity?.title.flatMap { $0.isEmpty ? nil : $0 }
            if count > 0 {
                if count == 1 { return GarminSyncFeedback(tone: .success, title: "\(name ?? "New activity") synced") }
                return GarminSyncFeedback(tone: .success, title: "\(count) new activities synced",
                                          detail: name.map { "Latest: \($0)" })
            }
            var lines: [String] = []
            if let latest = result.latestActivity {
                let when = whenLabel(latest.startedAtLocal, today: today)
                switch (name, when) {
                case let (name?, when?): lines.append("Latest at Garmin: \(name), \(when).")
                case let (nil, when?): lines.append("Latest at Garmin: \(when).")
                case let (name?, nil): lines.append("Latest at Garmin: \(name).")
                default: break
                }
            }
            lines.append("Opening Garmin Connect usually makes the watch upload.")
            return GarminSyncFeedback(tone: .neutral, title: "No new activity at Garmin yet",
                                      detail: lines.joined(separator: "\n"), offersConnect: true)
        case .cooldown, .busy:
            let wait = result.retryAfterS.map { max(1, $0) }
            return GarminSyncFeedback(tone: .neutral, title: wait.map { "Just synced, try again in \($0)s" } ?? "Just synced, try again shortly")
        case .limitReached:
            return GarminSyncFeedback(tone: .warning, title: result.manualSyncsLimit.map { "Daily sync limit reached (\($0))" } ?? "Daily sync limit reached")
        case .unknown:
            return GarminSyncFeedback(tone: .warning, title: "Unexpected answer from the server",
                                      detail: "Update the app if this keeps happening.")
        }
    }

    public static func feedback(for error: Error) -> GarminSyncFeedback {
        GarminSyncFeedback(tone: .warning, title: message(for: error))
    }

    public static func message(for error: Error) -> String {
        if let api = error as? APIError, case .http(let code, _) = api {
            switch code {
            case 429: return "Garmin is limiting requests right now. Try again in a few minutes."
            case 503: return "Garmin isn't available on the server."
            case 502: return "Garmin didn't answer. Try again later."
            default: break
            }
        }
        return APIError.message(for: error)
    }

    /// "today at 07:12", "yesterday at 07:12", "Mon 26 Oct at 07:12" from a naive local timestamp.
    public static func whenLabel(_ startedAtLocal: String?, today: String) -> String? {
        guard let raw = startedAtLocal, raw.count >= 16 else { return nil }
        let parts = raw.split(separator: "T", maxSplits: 1)
        guard parts.count == 2, parts[0].count == 10 else { return nil }
        let day = String(parts[0]), time = String(parts[1].prefix(5))
        guard time.count == 5, time.dropFirst(2).first == ":", let date = AppClock.date(fromDay: day) else { return nil }
        let todayDate = AppClock.date(fromDay: today)
        let label: String
        if day == today { label = "today" }
        else if let todayDate, AppClock.calendar.date(byAdding: .day, value: -1, to: todayDate).map(AppClock.dayString) == day {
            label = "yesterday"
        } else {
            label = date.formatted(AppLocale.dateTime.weekday(.abbreviated).day().month(.abbreviated))
        }
        return "\(label) at \(time)"
    }
}
