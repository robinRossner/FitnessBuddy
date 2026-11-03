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
                .appRoot(env)
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

extension View {
    /// What the root view gets from the app: the environment, the fixed app locale, the store and (DEBUG) the UI-test
    /// hooks. Shared with the in-process snapshot pass, so its screens are mounted exactly like the app's.
    func appRoot(_ env: AppEnvironment) -> some View {
        environment(env)
            .environment(\.locale, AppLocale.current)
            .modelContainer(env.container)
            #if DEBUG
            .uiTestHooks()
            #endif
    }
}
