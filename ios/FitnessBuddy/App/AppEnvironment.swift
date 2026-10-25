import Foundation
import Observation
import SwiftData
import SwiftUI

enum AppTab: Hashable {
    case today, workout, progress, health, coach
}

/// Where the Coach tab navigates to when opened from elsewhere.
enum CoachRoute: Hashable {
    case proposal(String)
    case history
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
    let coach: CoachRepository

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
    /// The "Start workout" chooser (planned sessions / empty).
    var showWorkoutChooser = false
    /// Set by "open this proposal" from anywhere (Today banner, Ask coach); the Coach tab consumes it.
    var coachRoute: CoachRoute?

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
        self.coach = CoachRepository(settings: settings, network: network,
                                     store: testing ? MemoryStatsCache() : DiskStatsCache(directory: Self.coachCacheDirectory),
                                     isDemo: { SyncState.current(in: container.mainContext).demoActive })
        self.sync = SyncCoordinator(container: container, settings: settings, network: network)
        self.sync.enabled = !testing
        #if DEBUG
        self.stats.demoTransport = DemoStats.transport
        self.coach.demoTransport = DemoCoach.shared.transport
        #endif
        coach.afterPlanChange = { [weak self] in await self?.sync.sync(.manual) }
        coach.onPendingChanged = { [weak self] count, latest in
            guard let self else { return }
            let state = SyncState.current(in: self.context)
            guard state.coachPendingCount != count || state.coachLatestPendingId != latest else { return }
            state.coachPendingCount = count
            state.coachLatestPendingId = latest
            try? self.context.save()
        }
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
        coach.reset()
        coachRoute = nil
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

    static var coachCacheDirectory: URL {
        (FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? FileManager.default.temporaryDirectory)
            .appendingPathComponent("CoachCache", isDirectory: true)
    }

    /// Shows a proposal (or just the Coach tab) from anywhere.
    func openCoach(proposal id: String? = nil) {
        coachRoute = id.map { CoachRoute.proposal($0) }
        selectedTab = .coach
    }

    var context: ModelContext { container.mainContext }
    var workout: WorkoutService { WorkoutService(context: context) }

    func start() {
        guard !Self.isRunningTests else { return }
        #if DEBUG
        // `-uiDemo YES` loads the demo data at launch (screenshots).
        if UserDefaults.standard.bool(forKey: "uiDemo"), !SyncState.current(in: context).demoActive {
            DemoData.load(into: context, settings: settings)
            sync.didResetData()
        }
        #endif
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

    /// "Start workout" everywhere: resumes the running workout, otherwise opens the chooser (or starts an empty workout
    /// right away when there is nothing to choose from).
    func beginWorkout() {
        if workout.activeSession() != nil { selectedTab = .workout; return }
        let choices = WorkoutChooserLogic.choices(in: context)
        if choices.count > 1 { showWorkoutChooser = true } else { startWorkout(from: nil) }
    }

    /// Starts what the chooser picked: a planned session (prefilled, linked), a finished one as template, or empty.
    func startWorkout(_ choice: WorkoutChoice) {
        switch choice.source {
        case .planned(let id):
            let row = try? context.fetch(FetchDescriptor<CachedPlannedSession>(predicate: #Predicate { $0.id == id })).first
            startWorkout(from: row)
        case .template(let id):
            let template = try? context.fetch(FetchDescriptor<LocalSession>(predicate: #Predicate { $0.id == id })).first
            if let template, workout.start(fromTemplate: template, gymId: settings.currentGymId) != nil { selectedTab = .workout }
            else { startWorkout(from: nil) }
        case .empty:
            startWorkout(from: nil)
        }
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
