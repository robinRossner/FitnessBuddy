import SwiftData
import SwiftUI

/// Five tabs: Summary (home), Workout, Progress, Health, Coach. Coach shows a badge while proposals wait for a decision.
struct RootView: View {
    @Environment(AppEnvironment.self) private var env
    @Query private var states: [SyncState]

    var body: some View {
        @Bindable var env = env
        TabView(selection: $env.selectedTab) {
            Tab("Summary", systemImage: "heart.text.square.fill", value: AppTab.summary) { TodayView() }
            Tab("Workout", systemImage: "dumbbell.fill", value: AppTab.workout) { WorkoutTabView() }
            Tab("Progress", systemImage: "chart.line.uptrend.xyaxis", value: AppTab.progress) { ProgressTabView() }
            Tab("Health", systemImage: "heart.fill", value: AppTab.health) { HealthTabView() }
            Tab("Coach", systemImage: "sparkles", value: AppTab.coach) { CoachView() }
                .badge(states.first?.coachPendingCount ?? 0)
        }
        .sheet(isPresented: $env.showWorkoutChooser) { WorkoutChooserSheet() }
    }
}
