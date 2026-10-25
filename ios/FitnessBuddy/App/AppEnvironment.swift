import Foundation
import Observation
import SwiftData
import SwiftUI

enum AppTab: Hashable {
    case today, workout, progress, health, coach
}

/// Composition root: owns the container, settings, sync and timer, and app-level navigation state.
@MainActor
@Observable
final class AppEnvironment {
    let container: ModelContainer
    let settings: SettingsStore
    let network: NetworkMonitor
    let sync: SyncCoordinator
    let restTimer: RestTimer
    let stats: StatsRepository

    #if DEBUG
    /// `-uiTab progress|health|...` selects the tab at launch (screenshots).
    var selectedTab: AppTab = {
        switch UserDefaults.standard.string(forKey: "uiTab") {
        case "workout": .workout
        case "progress": .progress
        case "health": .health
        case "coach": .coach
        default: .today
        }
    }()
    #else
    var selectedTab: AppTab = .today
    #endif
    var finishedSummary: WorkoutSummary?

    static var isRunningTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
    }

    init(container: ModelContainer? = nil, settings: SettingsStore? = nil) {
        let testing = Self.isRunningTests
        let container = container ?? ((try? Persistence.makeContainer(inMemory: testing)) ?? {
            fatalError("Could not create the local database")
        }())
        let settings = settings ?? SettingsStore(loadToken: !testing)
        let network = NetworkMonitor()
        self.container = container
        self.settings = settings
        self.network = network
        self.restTimer = RestTimer()
        self.stats = StatsRepository(settings: settings, store: testing ? MemoryStatsCache() : DiskStatsCache(),
                                     isDemo: { SyncState.current(in: container.mainContext).demoActive })
        self.sync = SyncCoordinator(container: container, settings: settings, network: network)
        self.sync.enabled = !testing
        #if DEBUG
        self.stats.demoTransport = DemoStats.transport
        #endif
        network.onBecameConnected = { [weak self] in self?.sync.request(.network) }
        // The cache must belong to the configured backend before anything syncs (also covers launch-argument
        // overrides, URL/token changed outside the Settings screen, and caches from older app versions).
        reconcileBackend()
    }

    // MARK: Backend / local data

    /// Wipes the local cache if it belongs to a different backend (or to demo data). Synchronous, no network.
    @discardableResult
    func reconcileBackend() -> Bool {
        let fp = settings.fingerprint
        let state = SyncState.current(in: context)
        // Nothing configured and no demo/old cache to drop: keep whatever the user logged offline.
        if fp == nil && !state.demoActive && state.backendFingerprint == nil { return false }
        // Demo data without a configured backend stays (it is the demo).
        if fp == nil && state.demoActive { return false }
        guard BackendReset.needsReset(fingerprint: fp, in: context) else { return false }
        wipeLocalData(fingerprint: fp)
        return true
    }

    /// True when applying this backend would wipe the cache.
    func backendChangeWipes(url: String, token: String) -> Bool {
        let fp = BackendReset.fingerprint(url: url, token: token)
        let state = SyncState.current(in: context)
        if fp == nil && state.demoActive { return false }
        return BackendReset.needsReset(fingerprint: fp, in: context)
    }

    var unsyncedRecordCount: Int { BackendReset.unsyncedCount(in: context) }

    private func wipeLocalData(fingerprint: String?) {
        BackendReset.reset(in: context, fingerprint: fingerprint)
        settings.currentGymId = nil
        sync.didResetData()
    }

    /// DEBUG demo mode off again: drops demo rows and caches, keeps real unsynced records.
    func clearDemoData() {
        wipeLocalData(fingerprint: SyncState.current(in: context).backendFingerprint)
    }

    /// Settings > Save: stores URL + token, wipes the cache when the backend (or the token) changed, then runs a
    /// full sync right away. Returns false if the token could not be written to the Keychain.
    @discardableResult
    func saveBackend(url: String, token: String) async -> Bool {
        await sync.cancelAndWait()
        let stored = settings.update(url: url, token: token)
        if backendChangeWipes(url: settings.backendURL, token: settings.apiToken) { wipeLocalData(fingerprint: settings.fingerprint) }
        await sync.sync(.settings)
        return stored
    }

    /// Settings > Reset local data & resync: same wipe, same fingerprint, then a full sync.
    func resetLocalDataAndResync() async {
        await sync.cancelAndWait()
        wipeLocalData(fingerprint: settings.fingerprint)
        await sync.sync(.manual)
    }

    var context: ModelContext { container.mainContext }
    var workout: WorkoutService { WorkoutService(context: context) }

    func start() {
        guard !Self.isRunningTests else { return }
        network.start()
        sync.request(.launch)
    }

    // MARK: Workout flow

    func startWorkout(from planned: CachedPlannedSession?) {
        if let planned {
            _ = workout.start(from: planned, gymId: settings.currentGymId)
        } else {
            _ = workout.startEmpty(gymId: settings.currentGymId)
        }
        selectedTab = .workout
    }

    func finishWorkout(_ session: LocalSession) {
        restTimer.cancel()
        if let summary = workout.finish(session) {
            finishedSummary = summary
            sync.request(.workoutFinished)
        }
    }

    func discardWorkout(_ session: LocalSession) {
        restTimer.cancel()
        workout.discard(session)
    }
}
