import Foundation
import Observation

/// What the short banner after "Sync" says. Built by `GarminSyncLogic`; the view only draws it.
struct GarminSyncFeedback: Equatable, Sendable {
    enum Tone: Equatable, Sendable { case success, neutral, warning }

    var tone: Tone
    var title: String
    var detail: String? = nil
    /// Shows the "Open Garmin Connect" action (nothing new at Garmin yet: the watch may not have uploaded).
    var offersConnect = false
}

/// Pure mapping from the backend's answer (or an error) to feedback. Copy never claims the watch was synced:
/// we only read Garmin's cloud, the watch uploads on its own through the Garmin Connect app.
enum GarminSyncLogic {
    static func feedback(for result: GarminSyncResultDTO, today: String = AppClock.todayString) -> GarminSyncFeedback {
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

    static func feedback(for error: Error) -> GarminSyncFeedback {
        GarminSyncFeedback(tone: .warning, title: message(for: error))
    }

    static func message(for error: Error) -> String {
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
    static func whenLabel(_ startedAtLocal: String?, today: String) -> String? {
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
            label = date.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
        }
        return "\(label) at \(time)"
    }
}

/// "Sync" button logic: ask the backend to pull Garmin now, then sync the app with the backend and reload the shown data.
/// If nothing new came, the banner offers "Open Garmin Connect"; when the app is next active after that, the whole flow
/// runs once more by itself (one-shot, never a loop).
@MainActor
@Observable
final class GarminSyncController {
    struct Hooks {
        /// `POST /garmin/sync`. Throws `APIError.notConfigured` without a backend.
        var request: @MainActor () async throws -> GarminSyncResultDTO
        var isDemo: @MainActor () -> Bool = { false }
        /// The app-side sync (`SyncCoordinator.sync(.manual)`).
        var syncApp: @MainActor () async -> Void
        /// Force-reloads the given stats (what pull-to-refresh reloads).
        var refresh: @MainActor ([any StatsLoadable]) async -> Void
        /// Opens Garmin Connect (or its App Store page); false when neither opened.
        var openConnect: @MainActor () async -> Bool = { await GarminConnectLink.open() }
        /// Throws when cancelled.
        var sleep: @MainActor (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
        var now: @MainActor () -> Date = { Date() }
    }

    /// The backend call and app sync are running (the button shows a spinner and is disabled).
    private(set) var isRunning = false
    private(set) var feedback: GarminSyncFeedback?
    /// Changes with every new banner, so a dismiss timer cannot close a newer one.
    private(set) var feedbackId = 0

    @ObservationIgnored private let hooks: Hooks
    /// Set by "Open Garmin Connect"; the next return to the app (after it was backgrounded) syncs once.
    @ObservationIgnored private var awaitingReturn = false
    @ObservationIgnored private var leftApp = false
    @ObservationIgnored private var openedConnectAt: Date?
    @ObservationIgnored private var autoTask: Task<Void, Never>?
    @ObservationIgnored private var lastRetryAfter: TimeInterval?
    @ObservationIgnored private var lastResponseAt: Date?
    @ObservationIgnored private var refreshTargets: [any StatsLoadable] = []

    /// A return later than this after opening Connect is not "coming back from Connect" any more.
    static let returnWindow: TimeInterval = 30 * 60

    init(hooks: Hooks) { self.hooks = hooks }

    /// A tap on the button. `refreshing`: the stats the current screen shows (also reloaded by the automatic re-sync).
    func sync(refreshing resources: [any StatsLoadable] = []) async {
        cancelAutoSync()
        guard !isRunning else { return }
        refreshTargets = resources
        await perform()
    }

    /// "Open Garmin Connect" in the banner.
    func openGarminConnect() async {
        awaitingReturn = true
        leftApp = false
        openedConnectAt = hooks.now()
        if !(await hooks.openConnect()) { awaitingReturn = false }
    }

    func dismissFeedback(id: Int? = nil) {
        if id == nil || id == feedbackId { feedback = nil }
    }

    func appDidEnterBackground() {
        if awaitingReturn { leftApp = true }
    }

    func appDidBecomeActive() {
        guard awaitingReturn, leftApp else { return }
        awaitingReturn = false
        leftApp = false
        guard let opened = openedConnectAt, hooks.now().timeIntervalSince(opened) <= Self.returnWindow else { return }
        let wait = lastRetryAfter.map { max(0, $0 - hooks.now().timeIntervalSince(lastResponseAt ?? .distantPast)) } ?? 0
        autoTask?.cancel()
        autoTask = Task { [weak self] in
            guard let self else { return }
            if wait > 0 {
                do { try await self.hooks.sleep(wait) } catch { return }
            }
            guard !Task.isCancelled, !self.isRunning else { return }
            await self.perform()
        }
    }

    private func cancelAutoSync() {
        autoTask?.cancel()
        autoTask = nil
        awaitingReturn = false
        leftApp = false
    }

    private func perform() async {
        isRunning = true
        defer { isRunning = false }
        if hooks.isDemo() {
            show(GarminSyncFeedback(tone: .neutral, title: "Not available with demo data."))
            return
        }
        let result: GarminSyncFeedback
        do {
            let response = try await hooks.request()
            lastRetryAfter = response.retryAfterS.map(TimeInterval.init)
            lastResponseAt = hooks.now()
            result = GarminSyncLogic.feedback(for: response)
        } catch {
            lastRetryAfter = nil
            lastResponseAt = nil
            if (error as? APIError)?.isCancellation == true || error is CancellationError { return }
            result = GarminSyncLogic.feedback(for: error)
            if case APIError.notConfigured = error {
                show(result)
                return
            }
        }
        // The backend may hold newer data than the app even when Garmin said no, so the app syncs in every case.
        await hooks.syncApp()
        await hooks.refresh(refreshTargets)
        show(result)
    }

    private func show(_ value: GarminSyncFeedback) {
        feedbackId += 1
        feedback = value
    }
}
