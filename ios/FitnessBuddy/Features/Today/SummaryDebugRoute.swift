#if DEBUG
import Foundation

/// Launch arguments of the Summary tab for screenshots / UI runs (DEBUG only; honoured only with `-uiTab summary`, or no
/// `-uiTab`, so the tabs never react to each other's values):
/// - `-uiSheet settings|pins|readiness` opens that sheet.
/// - `-uiRoute plannedlift` pushes the next lift, `metric/<id>` pushes a metric's chart (`hrv`, `rhr`, `sleep`, `sessions`,
///   `volume`, ...), `settings` opens Settings, `settings/gyms` pushes Manage gyms in it, `settings/machines` the current
///   gym's machines and `settings/machines/editor` also opens the machine editor on one machine.
enum SummaryDebugRoute {
    private static var isSummary: Bool { [nil, "summary"].contains(UserDefaults.standard.string(forKey: "uiTab")) }

    static var sheet: String? { isSummary ? UserDefaults.standard.string(forKey: "uiSheet") : nil }
    static var path: String? { isSummary ? UserDefaults.standard.string(forKey: "uiRoute") : nil }

    static func has(_ route: String) -> Bool { path == route }
}
#endif
