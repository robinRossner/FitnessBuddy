#if DEBUG
import FitnessCore
import Foundation

/// The Withings status shown with demo data (Settings, snapshots): connected, a weigh-in this morning at 07:12 and a
/// sync an hour ago (relative to the frozen screenpass clock, Wed 4 Nov 09:41). `-uiRoute settings/withings-off` and
/// `settings/withings-disconnected` show the other two states, `settings/withings-error` a failed last sync.
enum DemoWithings {
    @MainActor
    static func status() -> WithingsStatusDTO {
        let now = AppClock.instant
        let route = SummaryDebugRoute.path ?? ""
        switch route {
        case "settings/withings-off":
            return WithingsStatusDTO(configured: false, connected: false)
        case "settings/withings-disconnected":
            return WithingsStatusDTO(configured: true, connected: false)
        default:
            let cal = AppClock.calendar
            let weighIn = cal.date(bySettingHour: 7, minute: 12, second: 0, of: now)
            return WithingsStatusDTO(
                configured: true, connected: true,
                lastSyncAt: now.addingTimeInterval(-3600),
                lastError: route == "settings/withings-error" ? "Withings did not answer (timeout)." : nil,
                latestMeasuredAt: weighIn, latestWeightKg: 82.4)
        }
    }
}
#endif
