import SwiftUI

/// Look of the Coach tab: purple, so it reads as a different thing than lifting (orange), running (teal) and recovery (indigo).
enum CoachStyle {
    static let color = Color.purple
}

extension CoachRisk {
    var color: Color {
        switch self {
        case .low: .green
        case .medium: .orange
        case .high: .red
        case .unknown: .gray
        }
    }
}

extension CoachConfidence {
    var color: Color {
        switch self {
        case .high: .green
        case .medium: .blue
        case .low: .orange
        case .unknown: .gray
        }
    }
}

extension CoachStatus {
    var color: Color {
        switch self {
        case .pending: CoachStyle.color
        case .applied: .green
        case .partiallyApplied: .teal
        case .rejected: .gray
        case .failed: .red
        case .queued, .running: .blue
        case .awaitingKey: .orange
        case .dryRun: .indigo
        case .unknown: .gray
        }
    }
}

extension CoachVerdict {
    var color: Color {
        switch self {
        case .improved: .green
        case .neutral: .gray
        case .worse: .red
        case .unknown: .gray
        }
    }
    var title: String {
        switch self {
        case .improved: "Improved"
        case .neutral: "Neutral"
        case .worse: "Got worse"
        case .unknown: "Outcome"
        }
    }
    var symbol: String {
        switch self {
        case .improved: "arrow.up.right.circle.fill"
        case .neutral: "minus.circle.fill"
        case .worse: "arrow.down.right.circle.fill"
        case .unknown: "questionmark.circle.fill"
        }
    }
}

/// Round purple badge with the trigger's glyph.
struct TriggerBadge: View {
    let type: CoachTriggerType
    var size: CGFloat = 36

    var body: some View {
        Image(systemName: type.symbol)
            .font(.system(size: size * 0.45, weight: .semibold))
            .foregroundStyle(CoachStyle.color)
            .frame(width: size, height: size)
            .background(CoachStyle.color.opacity(0.15), in: Circle())
            .accessibilityHidden(true)
    }
}

struct RiskPill: View {
    let risk: CoachRisk
    var body: some View { Chip(text: risk.title, color: risk.color) }
}

struct ConfidencePill: View {
    let confidence: CoachConfidence?
    var body: some View {
        if let confidence, confidence != .unknown { Chip(text: confidence.title, color: confidence.color) }
    }
}

struct CoachStatusChip: View {
    let status: CoachStatus
    var body: some View { Chip(text: status.title, color: status.color) }
}

/// "from → to" in a quiet monospaced-digit capsule.
struct ChangeValueText: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.callout.weight(.medium).monospacedDigit())
            .foregroundStyle(.primary)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .fixedSize(horizontal: false, vertical: true)
    }
}

extension Date {
    /// "Today 14:03" / "Oct 24, 14:03".
    var coachLabel: String {
        let cal = Calendar.current
        let time = formatted(.dateTime.hour().minute())
        if cal.isDateInToday(self) { return "Today " + time }
        if cal.isDateInYesterday(self) { return "Yesterday " + time }
        return formatted(.dateTime.month(.abbreviated).day()) + ", " + time
    }
}

/// "Updated 2 min ago" / "Offline, showing data from ..." for the coach cache.
struct CoachFreshnessLabel: View {
    let repository: CoachRepository

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            HStack(spacing: 6) {
                if repository.isRefreshing { ProgressView().controlSize(.mini) }
                else if repository.isOffline { Image(systemName: "icloud.slash") }
                if let text = text(now: context.date) { Text(text) }
            }
            .font(.caption)
            .foregroundStyle(repository.failure != nil && !repository.isOffline ? Color.orange : Color.secondary)
            .frame(maxWidth: .infinity, alignment: .center)
            .accessibilityElement(children: .combine)
        }
    }

    private func text(now: Date) -> String? {
        if let failure = repository.failure, !repository.isOffline { return "Couldn't refresh: \(failure)" }
        return StatsFreshness.label(fetchedAt: repository.proposalsFetchedAt, offline: repository.isOffline, now: now)
    }
}

/// Small orange hint under a disabled action ("Needs a connection").
struct OfflineHint: View {
    let text: String
    var body: some View {
        Label(text, systemImage: "icloud.slash")
            .font(.footnote)
            .foregroundStyle(.secondary)
    }
}
