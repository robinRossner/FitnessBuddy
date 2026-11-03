import AuthenticationServices
import FitnessCore
import Foundation
import Observation

/// The Withings section of Settings: status, Connect (OAuth in a web authentication session; the server finishes the token
/// exchange and redirects to `fitnessbuddy://withings?...`), Sync now and Disconnect. The weigh-ins themselves reach the app
/// through the normal sync pull, so after new ones arrived (and after connecting) it runs `syncApp`.
@MainActor
@Observable
final class WithingsController {
    struct Hooks {
        var status: @MainActor () async throws -> WithingsStatusDTO
        /// `POST /withings/connect`: the authorize URL.
        var connect: @MainActor () async throws -> URL
        var sync: @MainActor () async throws -> WithingsSyncResultDTO
        var disconnect: @MainActor () async throws -> Void
        var isDemo: @MainActor () -> Bool = { false }
        /// The app-side sync (`SyncCoordinator.sync(.manual)`) and the reload of the stats that show weigh-ins.
        var syncApp: @MainActor () async -> Void
        var refreshStats: @MainActor () async -> Void = {}
        var now: @MainActor () -> Date = { AppClock.instant }
    }

    enum Activity: Equatable { case connecting, syncing, disconnecting }

    /// nil until the first answer (or after an error: see `loadError`).
    private(set) var status: WithingsStatusDTO?
    private(set) var loadError: String?
    private(set) var loading = false
    private(set) var activity: Activity?
    private(set) var feedback: WithingsLogic.Feedback?

    /// DEBUG: the status shown with demo data (snapshots), set by `AppEnvironment`.
    @ObservationIgnored var demoStatus: @MainActor () -> WithingsStatusDTO? = { nil }
    @ObservationIgnored private let hooks: Hooks

    init(hooks: Hooks) { self.hooks = hooks }

    var isBusy: Bool { activity != nil }
    var now: Date { hooks.now() }

    /// Loads the status (when the section appears, and after every action).
    func refresh() async {
        if hooks.isDemo() {
            status = demoStatus()
            loadError = status == nil ? "Not available with demo data." : nil
            return
        }
        loading = true
        defer { loading = false }
        do {
            status = try await hooks.status()
            loadError = nil
        } catch {
            if (error as? APIError)?.isCancellation == true || error is CancellationError { return }
            if case APIError.notConfigured = error { status = nil; loadError = nil; return }
            loadError = WithingsLogic.message(for: error)
        }
    }

    /// `authenticate` opens the URL and returns the callback URL (the view passes `webAuthenticationSession`).
    func connect(authenticate: @MainActor (URL) async throws -> URL) async {
        guard !isBusy else { return }
        if demoBlocked() { return }
        activity = .connecting
        defer { activity = nil }
        feedback = nil
        let callback: URL
        do {
            let url = try await hooks.connect()
            callback = try await authenticate(url)
        } catch {
            if Self.isUserCancel(error) || (error as? APIError)?.isCancellation == true { return }
            feedback = .init(tone: .warning, text: WithingsLogic.message(for: error))
            return
        }
        switch WithingsCallback.parse(callback) {
        case .connected:
            await refresh()
            await runSync(afterConnect: true)
        case .failed:
            feedback = .init(tone: .warning, text: WithingsCallback.parse(callback)?.message ?? "Connecting to Withings failed.")
        case nil:
            feedback = .init(tone: .warning, text: "Unexpected answer from Withings. Try again.")
        }
    }

    func syncNow() async {
        guard !isBusy else { return }
        if demoBlocked() { return }
        activity = .syncing
        defer { activity = nil }
        feedback = nil
        await runSync(afterConnect: false)
    }

    func disconnect() async {
        guard !isBusy else { return }
        if demoBlocked() { return }
        activity = .disconnecting
        defer { activity = nil }
        feedback = nil
        do {
            try await hooks.disconnect()
            feedback = .init(tone: .neutral, text: "Disconnected. Weigh-ins already imported stay.")
        } catch {
            feedback = .init(tone: .warning, text: WithingsLogic.message(for: error))
        }
        await refresh()
    }

    func dismissFeedback() { feedback = nil }

    // MARK: Private

    private func demoBlocked() -> Bool {
        guard hooks.isDemo() else { return false }
        feedback = .init(tone: .neutral, text: "Not available with demo data.")
        return true
    }

    private func runSync(afterConnect: Bool) async {
        let result: WithingsSyncResultDTO
        do {
            result = try await hooks.sync()
        } catch {
            if (error as? APIError)?.isCancellation == true || error is CancellationError { return }
            // Connected, but the first import failed: say so, the user can tap Sync now.
            let text = WithingsLogic.message(for: error)
            feedback = .init(tone: .warning, text: afterConnect ? "Connected. The first import failed: \(text)" : text)
            await refresh()
            return
        }
        var line = WithingsLogic.feedback(for: result, now: hooks.now())
        if afterConnect, result.status == .synced, (result.newMeasurements ?? 0) == 0 {
            line = .init(tone: .success, text: "Connected. No weigh-ins yet.")
        } else if afterConnect, line.tone == .success {
            line.text = "Connected. " + line.text
        }
        feedback = line
        if (result.newMeasurements ?? 0) > 0 || afterConnect {
            await hooks.syncApp()
            await hooks.refreshStats()
        }
        await refresh()
    }

    private static func isUserCancel(_ error: Error) -> Bool {
        (error as? ASWebAuthenticationSessionError)?.code == .canceledLogin
    }
}
