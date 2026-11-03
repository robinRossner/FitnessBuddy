import FitnessCore
import Foundation
import Observation
import SwiftData
import SwiftUI
import UserNotifications

enum AppTab: Hashable {
    case summary, workout, progress, health, coach
}

/// Where the Coach tab navigates to when opened from elsewhere.
enum CoachRoute: Hashable {
    case proposal(String)
    case history
}

/// Composition root: builds and owns the container, settings, sync, repositories and timer, injected into the views
/// with `.environment(env)`, plus app-level navigation state. The workout start/finish flow and the backend reset live
/// in `WorkoutFlow` and `BackendResetController`.
@MainActor
@Observable
final class AppEnvironment {
    let container: ModelContainer
    let settings: SettingsStore
    let network: NetworkMonitor
    let sync: SyncCoordinator
    /// The toolbar "Sync" button: backend pulls Garmin now, then the app syncs with the backend.
    let garmin: GarminSyncController
    /// Settings > Withings: connect the smart scale; the server imports the weigh-ins.
    let withings: WithingsController
    let restTimer: RestTimer
    let stats: StatsRepository
    let coach: CoachRepository
    let gyms: GymRepository
    let workoutFlow: WorkoutFlow
    let backend: BackendResetController
    /// Unit tests run hosted in the app: in-memory store and caches, no Keychain, no sync, no notifications or sound.
    /// Set once by the app entry (`FitnessBuddyApp`) from the test runner's environment, or by tests directly.
    let isTesting: Bool
    /// Retained here: the notification center only holds its delegate weakly.
    private let notificationPresenter = NotificationPresenter()

    #if DEBUG
    /// `-uiTab progress|health|...` selects the tab at launch (screenshots).
    var selectedTab: AppTab = {
        switch UserDefaults.standard.string(forKey: "uiTab") {
        case "workout": .workout
        case "progress": .progress
        case "health": .health
        case "coach": .coach
        default: .summary
        }
    }()
    #else
    var selectedTab: AppTab = .summary
    #endif
    /// Set by "open this proposal" from anywhere (Today banner, Ask coach); the Coach tab consumes it.
    var coachRoute: CoachRoute?

    init(container: ModelContainer? = nil, settings: SettingsStore? = nil, testing: Bool = false) {
        isTesting = testing
        #if DEBUG
        if !testing { UITestSupport.configureLaunch() }
        #endif
        let container = container ?? ((try? Persistence.makeContainer(inMemory: testing)) ?? {
            fatalError("Could not create the local database")
        }())
        let settings = settings ?? SettingsStore(loadToken: !testing)
        let network = NetworkMonitor()
        self.container = container
        self.settings = settings
        self.network = network
        let restTimer = RestTimer(silent: testing)
        self.restTimer = restTimer
        let isDemo: @MainActor () -> Bool = { SyncState.current(in: container.mainContext).demoActive }
        /// One raw-JSON cache folder per repository under Application Support (in memory while testing).
        func cache(_ name: String) -> any StatsCacheStore { testing ? MemoryStatsCache() : DiskStatsCache(named: name) }
        self.stats = StatsRepository(settings: settings, network: network, store: cache("StatsCache"), isDemo: isDemo)
        self.coach = CoachRepository(settings: settings, network: network, store: cache("CoachCache"), isDemo: isDemo)
        self.gyms = GymRepository(settings: settings, network: network, store: cache("GymCache"), isDemo: isDemo)
        let sync = SyncCoordinator(container: container, settings: settings, network: network)
        sync.enabled = !testing
        self.sync = sync
        self.garmin = GarminSyncController(hooks: .init(
            request: {
                guard let client = settings.makeClient(), settings.isConfigured else { throw APIError.notConfigured }
                return try await client.garminSync()
            },
            isDemo: isDemo,
            syncApp: { await sync.sync(.manual) },
            refresh: { resources in await loadStats(resources, generation: sync.generation, maxAge: 0, force: true) }))
        let stats = self.stats
        self.withings = WithingsController(hooks: .init(
            status: {
                guard let client = settings.makeClient(), settings.isConfigured else { throw APIError.notConfigured }
                return try await client.withingsStatus()
            },
            connect: {
                guard let client = settings.makeClient(), settings.isConfigured else { throw APIError.notConfigured }
                guard let url = URL(string: try await client.withingsConnect().authorizeUrl) else { throw APIError.http(502, nil) }
                return url
            },
            sync: {
                guard let client = settings.makeClient(), settings.isConfigured else { throw APIError.notConfigured }
                return try await client.withingsSync()
            },
            disconnect: {
                guard let client = settings.makeClient(), settings.isConfigured else { throw APIError.notConfigured }
                try await client.withingsDisconnect()
            },
            isDemo: isDemo,
            syncApp: { await sync.sync(.manual) },
            refreshStats: {
                await loadStats([stats.resource(StatsEndpoints.health())], generation: sync.generation, maxAge: 0, force: true)
            }))
        workoutFlow = WorkoutFlow(context: container.mainContext, settings: settings, restTimer: restTimer, sync: sync)
        backend = BackendResetController(context: container.mainContext, settings: settings, sync: sync, coach: coach, gyms: gyms)
        #if DEBUG
        self.stats.demoTransport = DemoStats.transport
        self.coach.demoTransport = DemoCoach.shared.transport
        self.gyms.demoTransport = DemoGyms.shared.transport
        self.withings.demoStatus = DemoWithings.status
        #endif
        coach.afterPlanChange = { [weak self] in await self?.sync.sync(.manual) }
        gyms.afterPlanChange = { [weak self] in await self?.sync.sync(.manual) }
        coach.onPendingChanged = { [weak self] count, latest in
            guard let self else { return }
            let state = SyncState.current(in: self.context)
            guard state.coachPendingCount != count || state.coachLatestPendingId != latest else { return }
            state.coachPendingCount = count
            state.coachLatestPendingId = latest
            try? self.context.save()
        }
        network.onBecameConnected = { [weak self] in self?.sync.request(.network) }
        workoutFlow.showWorkoutTab = { [weak self] in self?.selectedTab = .workout }
        backend.didWipe = { [weak self] in self?.coachRoute = nil }
        #if DEBUG
        if !testing { UITestSupport.resetIfRequested(self) }
        #endif
        // The cache must belong to the configured backend before anything syncs (also covers launch-argument
        // overrides, URL/token changed outside the Settings screen, and caches from older app versions).
        backend.reconcileBackend()
    }

    /// Shows a proposal (or just the Coach tab) from anywhere.
    func openCoach(proposal id: String? = nil) {
        coachRoute = id.map { CoachRoute.proposal($0) }
        selectedTab = .coach
    }

    var context: ModelContext { container.mainContext }
    var workout: WorkoutService { WorkoutService(context: context) }
    var exercises: ExerciseLibrary { ExerciseLibrary(settings: settings, network: network, context: context) }

    func start() {
        guard !isTesting else { return }
        #if DEBUG
        // `-uiDemo YES` loads the demo data at launch (screenshots).
        if UserDefaults.standard.bool(forKey: "uiDemo"), !SyncState.current(in: context).demoActive {
            DemoData.load(into: context, settings: settings)
            sync.didResetData()
        }
        #endif
        network.start()
        sync.request(.launch)
        UNUserNotificationCenter.current().delegate = notificationPresenter
        // ask before the first rest, so its notification is not scheduled without permission
        Task { await restTimer.requestAuthorizationIfNeeded() }
        didBecomeActive()
    }

    /// The app came to the foreground: the user is here, so the rest / finish notifications already shown are cleared.
    func didBecomeActive() {
        guard !isTesting else { return }
        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: WorkoutNotifications.all)
    }
}
