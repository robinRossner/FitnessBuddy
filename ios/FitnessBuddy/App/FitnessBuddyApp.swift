import SwiftData
import SwiftUI

@main
struct FitnessBuddyApp: App {
    @State private var env = AppEnvironment()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(env)
                .modelContainer(env.container)
                .task { env.start() }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active {
                        env.sync.request(.foreground)
                        env.didBecomeActive()
                        env.garmin.appDidBecomeActive()
                    } else if phase == .background {
                        env.garmin.appDidEnterBackground()
                    }
                }
        }
    }
}
