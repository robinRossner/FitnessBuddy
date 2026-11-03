import FitnessCore
import SwiftUI

// MARK: - Navigation

/// Screens of the Coach tab's stack. `CoachRoute` (App) is what other tabs can open; this adds the screens only the
/// Coach tab reaches (settings, the full proposal list).
enum CoachScreen: Hashable {
    case proposal(String)
    case configHistory
    case settings
    case allProposals

    init(_ route: CoachRoute) {
        switch route {
        case .proposal(let id): self = .proposal(id)
        case .history: self = .configHistory
        }
    }
}

// MARK: - Colours

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
        case .pending: Domain.coach.color
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

    /// Glyph for history rows.
    var symbol: String {
        switch self {
        case .pending: "sparkles"
        case .applied: "checkmark.circle.fill"
        case .partiallyApplied: "circle.lefthalf.filled"
        case .rejected: "xmark.circle.fill"
        case .failed: "exclamationmark.triangle.fill"
        case .queued, .running: "hourglass"
        case .awaitingKey: "key.fill"
        case .dryRun: "testtube.2"
        case .unknown: "questionmark.circle"
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

// MARK: - Change formatting

/// "Bench Press   82.5 → 85 kg": name left, old value struck and grey, new value bold. Falls back to two lines
/// when it does not fit.
struct CoachChangeLine: View {
    let line: CoachChangeFormat.Line

    var body: some View {
        switch line.kind {
        case .value(let old, let new):
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(line.title).lineLimit(1)
                    Spacer(minLength: 8)
                    CoachValueChange(old: old, new: new).fixedSize()
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(line.title).fixedSize(horizontal: false, vertical: true)
                    CoachValueChange(old: old, new: new)
                }
            }
            .font(.subheadline)
            .accessibilityElement(children: .combine)
        case .schedule(let symbol):
            Label {
                Text(line.title).fixedSize(horizontal: false, vertical: true)
            } icon: {
                Image(systemName: symbol).foregroundStyle(Domain.coach.color)
            }
            .font(.subheadline)
        }
    }
}

/// "82.5 → 85 kg" with the old value struck through.
struct CoachValueChange: View {
    let old: String
    let new: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(old).strikethrough().foregroundStyle(.secondary)
                .accessibilityLabel("from \(old)")
            Image(systemName: "arrow.right")
                .font(.caption.weight(.bold))
                .foregroundStyle(Domain.coach.color)
                .accessibilityHidden(true)
            Text(new).fontWeight(.semibold)
                .accessibilityLabel("to \(new)")
        }
        .monospacedDigit()
    }
}

// MARK: - Check-ins

struct CheckInGauge: View {
    let segments: [Bool]

    var body: some View {
        HStack(spacing: 6) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, filled in
                Capsule()
                    .fill(filled ? AnyShapeStyle(Domain.coach.color.gradient) : AnyShapeStyle(Color(.systemGray5)))
                    .frame(height: 8)
            }
        }
        .accessibilityHidden(true)
    }
}

// MARK: - Small parts

/// A row inside a card that pushes a Coach screen: content left, chevron right (value-based `ChevronRowLink`).
struct CoachRowLink<Label: View>: View {
    let screen: CoachScreen
    @ViewBuilder var label: Label

    var body: some View {
        NavigationLink(value: screen) {
            HStack(spacing: 12) {
                label
                Spacer(minLength: 8)
                DisclosureChevron()
            }
            .padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// "Why and What Changes" footer row of a card (value-based `ShowAllLink`).
struct CoachFooterLink: View {
    let title: String
    let screen: CoachScreen

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            NavigationLink(value: screen) {
                HStack {
                    Text(title).font(.subheadline)
                    Spacer()
                    DisclosureChevron()
                }
                .padding(.top, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }
}

/// One proposal in a history list: status glyph, title, "date · status".
struct CoachHistoryRow: View {
    let proposal: CoachProposalDTO

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: proposal.status.symbol)
                .font(.title3)
                .foregroundStyle(proposal.status.color)
                .frame(width: 26)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(proposal.trigger.title).font(.body.weight(.medium)).foregroundStyle(.primary)
                    .lineLimit(2).multilineTextAlignment(.leading)
                Text([proposal.createdAt?.coachLabel, proposal.status.title].compactMap { $0 }.joined(separator: " · "))
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// "Status   Connected": a read-only setting row.
struct CoachValueRow: View {
    let title: String
    let value: String
    var color: Color = .secondary

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
            Spacer(minLength: 8)
            Text(value).foregroundStyle(color).multilineTextAlignment(.trailing)
        }
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
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

extension Date {
    /// "Today 14:03" / "24 Oct, 14:03".
    var coachLabel: String {
        let cal = Calendar.current
        let time = formatted(AppLocale.dateTime.hour().minute())
        let now = AppClock.instant
        if cal.isDate(self, inSameDayAs: now) { return "Today " + time }
        if let yesterday = cal.date(byAdding: .day, value: -1, to: now), cal.isDate(self, inSameDayAs: yesterday) { return "Yesterday " + time }
        return formatted(AppLocale.dateTime.month(.abbreviated).day()) + ", " + time
    }
}

/// "Updated 2 min ago" / "Offline, showing data from ..." for the coach cache.
struct CoachFreshnessLabel: View {
    let repository: CoachRepository

    var body: some View {
        TimelineView(.periodic(from: AppClock.instant, by: 60)) { _ in
            HStack(spacing: 6) {
                if repository.isRefreshing { ProgressView().controlSize(.mini) }
                else if repository.isOffline { Image(systemName: "icloud.slash") }
                if let text = text(now: AppClock.instant) { Text(text) }
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

/// Small hint under a disabled action ("Needs a connection").
struct OfflineHint: View {
    let text: String
    var body: some View {
        Label(text, systemImage: "icloud.slash")
            .font(.footnote)
            .foregroundStyle(.secondary)
    }
}
