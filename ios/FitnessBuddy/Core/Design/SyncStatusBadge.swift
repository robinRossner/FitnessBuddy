import SwiftData
import SwiftUI

/// Subtle sync indicator (toolbar): spinner while syncing, cloud glyph otherwise, count of unsynced records.
struct SyncStatusBadge: View {
    @Environment(AppEnvironment.self) private var env
    @Query(filter: #Predicate<LocalSet> { $0.dirty && $0.rejectedReason == nil }) private var dirtySets: [LocalSet]
    @Query(filter: #Predicate<LocalSession> { $0.dirty && $0.rejectedReason == nil }) private var dirtySessions: [LocalSession]

    private var pending: Int {
        dirtySets.filter { $0.completedAt != nil || $0.deletedAt != nil || $0.hasBeenPushed }.count
            + dirtySessions.filter { $0.endedAt != nil || $0.deletedAt != nil || $0.hasBeenPushed }.count
    }

    var body: some View {
        let status = env.sync.status
        HStack(spacing: 4) {
            switch status {
            case .syncing:
                ProgressView().controlSize(.small)
            case .offline:
                Image(systemName: "icloud.slash")
            case .failed:
                Image(systemName: "exclamationmark.icloud")
            case .notConfigured:
                Image(systemName: "icloud.slash")
            case .demo:
                Image(systemName: "testtube.2")
            case .idle:
                Image(systemName: pending > 0 ? "icloud.and.arrow.up" : "checkmark.icloud")
            }
            if pending > 0, status != .notConfigured { Text("\(pending)").font(.caption2.monospacedDigit()) }
        }
        .font(.subheadline)
        .foregroundStyle(color(for: status))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label(for: status))
    }

    private func color(for status: SyncCoordinator.Status) -> Color {
        switch status {
        case .failed: .orange
        case .idle: pending > 0 ? .secondary : .secondary
        default: .secondary
        }
    }

    private func label(for status: SyncCoordinator.Status) -> String {
        switch status {
        case .idle: pending > 0 ? "\(pending) changes waiting to sync" : "Synced"
        case .syncing: "Syncing"
        case .offline: "Offline. \(pending) changes waiting to sync"
        case .notConfigured: "Backend not configured"
        case .demo: "Demo data loaded, sync paused"
        case .failed(let message): "Sync failed: \(message)"
        }
    }
}
