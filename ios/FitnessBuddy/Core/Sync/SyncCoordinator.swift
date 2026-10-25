import Foundation
import Observation
import SwiftData

enum SyncReason: String, Sendable {
    case launch, foreground, network, workoutFinished, bodyLogged, restTimeChanged, pullToRefresh, manual, retry, settings
}

/// UI-facing owner of sync: triggers, de-duplication, exponential backoff and a status for the badge.
/// It never blocks the UI: callers fire-and-forget (`request`) or await (`sync`, pull-to-refresh).
@MainActor
@Observable
final class SyncCoordinator {
    enum Status: Equatable {
        case idle, syncing, offline, notConfigured, demo
        case failed(String)
    }

    private(set) var status: Status = .idle
    private(set) var lastSyncAt: Date?
    private(set) var lastReport: SyncReport?
    /// Bumped whenever a sync finished (success or failure) or local data was reset, so views can refresh.
    private(set) var generation = 0

    @ObservationIgnored private let engine: SyncEngine
    @ObservationIgnored private let container: ModelContainer
    @ObservationIgnored private let settings: SettingsStore
    @ObservationIgnored private let network: NetworkMonitor
    @ObservationIgnored private var inFlight: Task<Void, Never>?
    @ObservationIgnored private var rerunRequested = false
    @ObservationIgnored private var retryTask: Task<Void, Never>?
    @ObservationIgnored private var failures = 0
    @ObservationIgnored var enabled = true

    init(container: ModelContainer, settings: SettingsStore, network: NetworkMonitor) {
        self.container = container
        self.settings = settings
        self.network = network
        self.engine = SyncEngine(container: container)
        self.lastSyncAt = SyncState.current(in: ModelContext(container)).lastSyncAt
    }

    /// Fire and forget.
    func request(_ reason: SyncReason) {
        Task { await sync(reason) }
    }

    /// Runs (or joins) a sync; returns when it finished. Safe to call from `.refreshable`.
    func sync(_ reason: SyncReason) async {
        guard enabled else { return }
        if let inFlight {
            rerunRequested = reason != .pullToRefresh
            await inFlight.value
            return
        }
        if SyncState.current(in: ModelContext(container)).demoActive {
            status = .demo
            return
        }
        guard let client = settings.makeClient(), settings.isConfigured else {
            status = .notConfigured
            return
        }
        // `.settings` follows an explicit user action (saved / left Settings): never skip it on a stale offline flag.
        let manual = reason == .manual || reason == .pullToRefresh || reason == .settings
        if !network.isConnected && !manual {
            status = .offline
            return
        }
        retryTask?.cancel()
        status = .syncing
        let engine = self.engine
        let task = Task { [weak self] in
            do {
                let report = try await engine.sync(using: client)
                self?.finish(.success(report))
            } catch {
                if Task.isCancelled || (error as? URLError)?.code == .cancelled || (error as? APIError)?.isCancellation == true
                    || error is CancellationError {
                    self?.finishCancelled()
                    return
                }
                await engine.recordFailure(APIError.message(for: error))
                self?.finish(.failure(error))
            }
        }
        inFlight = task
        await task.value
    }

    /// Stops a running sync and waits until it is really gone (before wiping the data it would write into).
    func cancelAndWait() async {
        retryTask?.cancel()
        retryTask = nil
        rerunRequested = false
        failures = 0
        if let task = inFlight {
            task.cancel()
            await task.value
        }
    }

    /// Called after the local data was wiped so the status and views start from a clean slate.
    func didResetData() {
        lastSyncAt = nil
        lastReport = nil
        if SyncState.current(in: ModelContext(container)).demoActive { status = .demo }
        else { status = settings.isConfigured ? .idle : .notConfigured }
        generation += 1
    }

    private func finishCancelled() {
        inFlight = nil
        if status == .syncing { status = .idle }
        generation += 1
    }

    private func finish(_ result: Result<SyncReport, Error>) {
        inFlight = nil
        generation += 1
        switch result {
        case .success(let report):
            failures = 0
            lastReport = report
            settings.currentGymId = GymSync.resolve(local: settings.currentGymId, server: report.serverGymId)
            lastSyncAt = SyncState.current(in: ModelContext(container)).lastSyncAt
            status = .idle
        case .failure(let error):
            failures += 1
            if let api = error as? APIError, case .transport = api, !network.isConnected {
                status = .offline
            } else {
                status = .failed(APIError.message(for: error))
            }
            if (error as? APIError)?.isTransient ?? true { scheduleRetry() }
        }
        if rerunRequested {
            rerunRequested = false
            request(.retry)
        }
    }

    /// 5 s, 10 s, 20 s ... capped at 5 min.
    nonisolated static func backoffDelay(failures: Int) -> TimeInterval {
        min(300, 5 * pow(2, Double(max(0, failures - 1))))
    }

    private func scheduleRetry() {
        retryTask?.cancel()
        let delay = Self.backoffDelay(failures: failures)
        retryTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled else { return }
            await self?.sync(.retry)
        }
    }
}
