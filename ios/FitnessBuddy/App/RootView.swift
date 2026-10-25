import SwiftUI

struct RootView: View {
    @Environment(AppEnvironment.self) private var env

    var body: some View {
        @Bindable var env = env
        TabView(selection: $env.selectedTab) {
            Tab("Today", systemImage: "sun.max.fill", value: AppTab.today) { TodayView() }
            Tab("Workout", systemImage: "dumbbell.fill", value: AppTab.workout) { WorkoutTabView() }
            Tab("Progress", systemImage: "chart.line.uptrend.xyaxis", value: AppTab.progress) { ProgressTabView() }
            Tab("Health", systemImage: "heart.fill", value: AppTab.health) { HealthTabView() }
            Tab("Coach", systemImage: "bubble.left.and.text.bubble.right.fill", value: AppTab.coach) { CoachPlaceholderView() }
        }
        .tint(Domain.lifting.color)
    }
}
