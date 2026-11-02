import Foundation

/// When the automatic sync on returning to the foreground may be skipped. Switching apps for a moment should not
/// cost a full push / pull / plan round trip; manual syncs (button, pull to refresh, Settings) never go through this.
public enum SyncThrottle {
    /// A successful sync younger than this counts as fresh.
    public static let foregroundInterval: TimeInterval = 60

    /// True when the last successful sync is under a minute old and nothing waits to be uploaded. A timestamp from
    /// the future (clock change) never counts as fresh.
    public static func skipsForegroundSync(lastSuccess: Date?, hasPendingUploads: Bool, now: Date = .now) -> Bool {
        guard let lastSuccess, !hasPendingUploads else { return false }
        let age = now.timeIntervalSince(lastSuccess)
        return age >= 0 && age < foregroundInterval
    }
}
