import Foundation
import Testing
@testable import FitnessCore

@Suite(.tags(.sync, .fast))
struct SyncThrottleTests {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    @Test func aFreshSyncWithNothingPendingIsSkipped() {
        #expect(SyncThrottle.skipsForegroundSync(lastSuccess: now.addingTimeInterval(-59), hasPendingUploads: false, now: now))
        #expect(SyncThrottle.skipsForegroundSync(lastSuccess: now, hasPendingUploads: false, now: now))
    }

    @Test func pendingUploadsAlwaysSync() {
        #expect(!SyncThrottle.skipsForegroundSync(lastSuccess: now.addingTimeInterval(-5), hasPendingUploads: true, now: now))
    }

    @Test func aMinuteOrOlderSyncs() {
        #expect(!SyncThrottle.skipsForegroundSync(lastSuccess: now.addingTimeInterval(-60), hasPendingUploads: false, now: now))
        #expect(!SyncThrottle.skipsForegroundSync(lastSuccess: now.addingTimeInterval(-3600), hasPendingUploads: false, now: now))
    }

    @Test func neverSyncedOrAFutureTimestampSyncs() {
        #expect(!SyncThrottle.skipsForegroundSync(lastSuccess: nil, hasPendingUploads: false, now: now))
        #expect(!SyncThrottle.skipsForegroundSync(lastSuccess: now.addingTimeInterval(30), hasPendingUploads: false, now: now))
    }
}
