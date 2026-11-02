import FitnessCore
import Foundation
import Observation

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
