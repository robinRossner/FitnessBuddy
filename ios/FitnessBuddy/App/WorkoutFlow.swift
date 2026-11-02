import FitnessCore
import Foundation
import Observation
import SwiftData

/// Starting and finishing workouts from anywhere: the "Start workout" chooser, the summary sheet after Finish, and
/// what Finish / Discard also do (stop the rest timer, queue a template change, request a sync).
@MainActor
@Observable
final class WorkoutFlow {
    /// Shown as a sheet after Finish.
    var finishedSummary: WorkoutSummary?
    /// The "Start workout" chooser (planned sessions / templates / empty).
    var showChooser = false

    @ObservationIgnored private let context: ModelContext
    @ObservationIgnored private let settings: SettingsStore
    @ObservationIgnored private let restTimer: RestTimer
    @ObservationIgnored private let sync: SyncCoordinator
    /// Switches to the Workout tab once a workout runs (set by the owner).
    @ObservationIgnored var showWorkoutTab: @MainActor () -> Void = {}

    init(context: ModelContext, settings: SettingsStore, restTimer: RestTimer, sync: SyncCoordinator) {
        self.context = context
        self.settings = settings
        self.restTimer = restTimer
        self.sync = sync
    }

    private var workout: WorkoutService { WorkoutService(context: context) }

    func start(from planned: CachedPlannedSession?) {
        if let planned {
            _ = workout.start(from: planned, gymId: settings.currentGymId)
        } else {
            _ = workout.startEmpty(gymId: settings.currentGymId)
        }
        showWorkoutTab()
    }

    /// "Start workout" everywhere: resumes the running workout, otherwise opens the chooser (or starts an empty workout
    /// right away when there is nothing to choose from).
    func begin() {
        if workout.activeSession() != nil { showWorkoutTab(); return }
        let choices = WorkoutChooserLogic.choices(in: context)
        if choices.count > 1 { showChooser = true } else { start(from: nil) }
    }

    /// Starts what the chooser picked: a planned session (prefilled, linked), a finished one as template, or empty.
    func start(_ choice: WorkoutChoice) {
        switch choice.source {
        case .planned(let id):
            let row = try? context.fetch(FetchDescriptor<CachedPlannedSession>(predicate: #Predicate { $0.id == id })).first
            start(from: row)
        case .template(let id):
            let template = try? context.fetch(FetchDescriptor<LocalSession>(predicate: #Predicate { $0.id == id })).first
            if let template, workout.start(fromTemplate: template, gymId: settings.currentGymId) != nil { showWorkoutTab() }
            else { start(from: nil) }
        case .empty:
            start(from: nil)
        }
    }

    /// `template`: the performed structure to save as the plan's new upper/lower template (nil = keep it).
    func finish(_ session: LocalSession, updateTemplate template: [TemplateExercise]? = nil) {
        let kind = session.kind
        restTimer.cancel()
        restTimer.cancelFinishReminder()
        if let summary = workout.finish(session) {
            finishedSummary = summary
            if let template { TemplateSync.enqueue(kind: kind, exercises: template) }
            sync.request(.workoutFinished)
        }
    }

    func discard(_ session: LocalSession) {
        restTimer.cancel()
        restTimer.cancelFinishReminder()
        workout.discard(session)
    }
}
