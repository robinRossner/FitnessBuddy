import Foundation

/// `GET /withings/status`. Everything but `configured` and `connected` may be null.
public struct WithingsStatusDTO: Decodable, Sendable, Equatable {
    /// The server has Withings credentials (false: nothing to connect to, the app shows no Connect button).
    public let configured: Bool
    public let connected: Bool
    public let lastSyncAt: Date?
    public let lastError: String?
    public let latestMeasuredAt: Date?
    public let latestWeightKg: Double?

    public init(configured: Bool, connected: Bool, lastSyncAt: Date? = nil, lastError: String? = nil,
                latestMeasuredAt: Date? = nil, latestWeightKg: Double? = nil) {
        self.configured = configured
        self.connected = connected
        self.lastSyncAt = lastSyncAt
        self.lastError = lastError
        self.latestMeasuredAt = latestMeasuredAt
        self.latestWeightKg = latestWeightKg
    }
}

/// `POST /withings/connect`: the page the user signs in at (opened in `ASWebAuthenticationSession`).
public struct WithingsConnectDTO: Decodable, Sendable, Equatable {
    public let authorizeUrl: String
}

/// `POST /withings/sync`.
public struct WithingsSyncResultDTO: Decodable, Sendable, Equatable {
    public enum Status: Decodable, Sendable, Equatable {
        case synced, busy, notConnected
        /// A status this app version does not know.
        case unknown(String)

        public init(from decoder: Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            switch raw {
            case "synced": self = .synced
            case "busy": self = .busy
            case "not_connected": self = .notConnected
            default: self = .unknown(raw)
            }
        }
    }

    public let status: Status
    public let newMeasurements: Int?
    public let latestMeasuredAt: Date?
    public let latestWeightKg: Double?

    public init(status: Status, newMeasurements: Int? = nil, latestMeasuredAt: Date? = nil, latestWeightKg: Double? = nil) {
        self.status = status
        self.newMeasurements = newMeasurements
        self.latestMeasuredAt = latestMeasuredAt
        self.latestWeightKg = latestWeightKg
    }
}

/// Where the backend's OAuth callback ends up: `fitnessbuddy://withings?result=connected` or
/// `fitnessbuddy://withings?result=error&reason=denied|invalid_state|exchange_failed`.
public enum WithingsCallback: Sendable, Equatable {
    public enum Reason: Sendable, Equatable {
        case denied, invalidState, exchangeFailed
        case other(String?)
    }

    case connected
    case failed(Reason)

    /// The scheme `ASWebAuthenticationSession` waits for (no Info.plist entry needed for that).
    public static let scheme = "fitnessbuddy"
    public static let host = "withings"

    /// nil when `url` is not a Withings callback.
    public static func parse(_ url: URL) -> WithingsCallback? {
        guard url.scheme?.lowercased() == scheme, url.host?.lowercased() == host,
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems else { return nil }
        func value(_ name: String) -> String? { items.first { $0.name == name }?.value }
        switch value("result") {
        case "connected": return .connected
        case "error":
            switch value("reason") {
            case "denied": return .failed(.denied)
            case "invalid_state": return .failed(.invalidState)
            case "exchange_failed": return .failed(.exchangeFailed)
            default: return .failed(.other(value("reason")))
            }
        default: return nil
        }
    }

    public var message: String? {
        guard case .failed(let reason) = self else { return nil }
        switch reason {
        case .denied: return "Withings access was not allowed."
        case .invalidState: return "The sign-in expired. Try connecting again."
        case .exchangeFailed: return "Withings did not accept the sign-in. Try again."
        case .other: return "Connecting to Withings failed."
        }
    }
}

/// What the Withings section and its messages say. Pure, so it is tested without the UI.
public enum WithingsLogic: Sendable {
    /// One line of feedback below the buttons.
    public struct Feedback: Equatable, Sendable {
        public enum Tone: Equatable, Sendable { case success, neutral, warning }
        public var tone: Tone
        public var text: String
        public init(tone: Tone, text: String) {
            self.tone = tone
            self.text = text
        }
    }

    /// "82.4 kg"
    public static func weightLabel(_ kg: Double) -> String { String(format: "%.1f kg", kg) }

    /// "today 07:12", "yesterday 18:05", "Mon 26 Oct 06:00".
    public static func whenLabel(_ date: Date, now: Date) -> String {
        let cal = AppClock.calendar
        let time = AppLocale.time(date)
        if cal.isDate(date, inSameDayAs: now) { return "today \(time)" }
        if let y = cal.date(byAdding: .day, value: -1, to: now), cal.isDate(date, inSameDayAs: y) { return "yesterday \(time)" }
        return date.formatted(AppLocale.dateTime.weekday(.abbreviated).day().month(.abbreviated)) + " " + time
    }

    /// "82.4 kg, today 07:12"; just the part that is known; nil when neither is.
    public static func latestWeighIn(weightKg: Double?, at date: Date?, now: Date) -> String? {
        switch (weightKg, date) {
        case let (kg?, date?): return "\(weightLabel(kg)), \(whenLabel(date, now: now))"
        case let (kg?, nil): return weightLabel(kg)
        case let (nil, date?): return whenLabel(date, now: now)
        default: return nil
        }
    }

    /// "5 min ago", "1 hour ago" (English, whatever the region of the phone).
    public static func relative(_ date: Date, now: Date) -> String {
        let f = RelativeDateTimeFormatter()
        f.locale = AppLocale.current
        f.unitsStyle = .full
        // under a minute reads "in 0 seconds" when the clocks differ a little
        if abs(date.timeIntervalSince(now)) < 60 { return "just now" }
        return f.localizedString(for: date, relativeTo: now)
    }

    /// The rows of the connected state, only the ones with a value.
    public struct StatusLines: Equatable, Sendable {
        public var headline: String
        public var lastSync: String?
        public var latestWeighIn: String?
    }

    public static func statusLines(_ status: WithingsStatusDTO, now: Date) -> StatusLines {
        if !status.configured { return .init(headline: "Not set up on server") }
        if !status.connected { return .init(headline: "Not connected") }
        return .init(
            headline: "Connected",
            lastSync: status.lastSyncAt.map { relative($0, now: now) } ?? "not yet",
            latestWeighIn: latestWeighIn(weightKg: status.latestWeightKg, at: status.latestMeasuredAt, now: now))
    }

    public static func feedback(for result: WithingsSyncResultDTO, now: Date) -> Feedback {
        switch result.status {
        case .synced:
            let count = result.newMeasurements ?? 0
            if count > 0 {
                let latest = latestWeighIn(weightKg: result.latestWeightKg, at: result.latestMeasuredAt, now: now)
                let title = count == 1 ? "1 new weigh-in" : "\(count) new weigh-ins"
                return Feedback(tone: .success, text: latest.map { "\(title). Latest: \($0)." } ?? "\(title).")
            }
            return Feedback(tone: .neutral, text: "No new weigh-ins.")
        case .busy:
            return Feedback(tone: .neutral, text: "A sync is already running. Try again shortly.")
        case .notConnected:
            return Feedback(tone: .warning, text: "Withings is not connected.")
        case .unknown:
            return Feedback(tone: .warning, text: "Unexpected answer from the server. Update the app if this keeps happening.")
        }
    }

    /// The line the toolbar Sync button adds to its banner after it also pulled Withings: only news (new weigh-ins) and
    /// transient Withings trouble. nil when there is nothing worth saying, also when Withings isn't connected or set up.
    public static func syncButtonNote(for result: WithingsSyncResultDTO, now: Date) -> String? {
        guard result.status == .synced, (result.newMeasurements ?? 0) > 0 else { return nil }
        return feedback(for: result, now: now).text
    }

    public static func syncButtonNote(for error: Error) -> String? {
        if let api = error as? APIError, case .http(let code, _) = api, code == 429 || code == 502 {
            return message(for: error)
        }
        return nil
    }

    public static func message(for error: Error) -> String {
        if let api = error as? APIError, case .http(let code, _) = api {
            switch code {
            case 429: return "Withings is limiting requests right now. Try again in a few minutes."
            case 503: return "Withings isn't set up on the server."
            case 502: return "Withings didn't answer. Try again later."
            default: break
            }
        }
        return APIError.message(for: error)
    }
}
