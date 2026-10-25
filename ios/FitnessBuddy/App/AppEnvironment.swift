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

    var selectedTab: AppTab = .today
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
        self.sync = SyncCoordinator(container: container, settings: settings, network: network)
        self.sync.enabled = !testing
        network.onBecameConnected = { [weak self] in self?.sync.request(.network) }
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
