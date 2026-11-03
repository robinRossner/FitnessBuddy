import FitnessCore
import SwiftData
import SwiftUI

@main
struct FitnessBuddyApp: App {
    /// The one place that decides test mode: unit tests run hosted in this app (the runner sets this variable).
    @State private var env = AppEnvironment(testing: ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil)
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(env)
                .modelContainer(env.container)
                #if DEBUG
                .uiTestHooks()
                #endif
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
