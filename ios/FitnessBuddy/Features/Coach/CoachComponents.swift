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

/// Pure helpers behind the "old → new" lines (unit-tested in CoachTabTests).
enum CoachChangeFormat {
    /// ("82.5", "85 kg"): the unit is written once, after the new value, when both sides carry it.
    static func parts(from: JSONValue, to: JSONValue, path: String?) -> (old: String, new: String) {
        let old = CoachValueFormat.text(from, path: path)
        let new = CoachValueFormat.text(to, path: path)
        guard let unit = path.flatMap(CoachValueFormat.unit(forPath:)) else { return (old, new) }
        let suffix = unit == "%" ? " %" : " " + unit
        guard old.hasSuffix(suffix), new.hasSuffix(suffix), old.count > suffix.count else { return (old, new) }
        return (String(old.dropLast(suffix.count)), new)
    }

    /// The server's label, or a readable name made from the parameter path when it only echoed the path
    /// ("exercise_overrides.triceps_extension.target_weight" -> "Triceps extension target weight").
    static func title(label: String, param: String) -> String {
        let trimmed = label.trimmingCharacters(in: .whitespaces)
        if !trimmed.isEmpty, trimmed != param { return trimmed }
        var segments = param.split(separator: ".").map(String.init)
        if segments.count > 2 { segments.removeFirst() }
        let words = segments.joined(separator: " ").replacingOccurrences(of: "_", with: " ")
        guard let first = words.first else { return param }
        return first.uppercased() + words.dropFirst()
    }

    /// One line of a proposal's changes, parameter or schedule.
    struct Line: Identifiable, Equatable {
        enum Kind: Equatable {
            case value(old: String, new: String)
            case schedule(symbol: String)
        }
        let id: String
        let title: String
        let kind: Kind
    }

    /// The lines the pending card shows: still-open changes first (parameter, then schedule); every change when
    /// none is open (e.g. all invalid).
    static func lines(for p: CoachProposalDTO) -> [Line] {
        let open = p.changes.filter { CoachLogic.isSelectable($0) }
        let openSchedule = p.scheduleChanges.filter { CoachLogic.isSelectable($0) }
        let useOpen = !open.isEmpty || !openSchedule.isEmpty
        let params = useOpen ? open : p.changes
        let schedule = useOpen ? openSchedule : p.scheduleChanges
        return params.map { c in
            let parts = Self.parts(from: c.from, to: c.to, path: c.param)
            return Line(id: c.id, title: title(label: c.label, param: c.param), kind: .value(old: parts.old, new: parts.new))
        } + schedule.map { Line(id: $0.id, title: $0.label, kind: .schedule(symbol: $0.action.symbol)) }
    }
}

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

/// Weekly check-in usage: one segment per allowed call, filled per call made.
enum CoachCheckIns {
    /// Longest gauge drawn; a larger budget shows the number only.
    static let maxSegments = 10

    /// Filled flags, nil when there is no usable budget to draw.
    static func segments(used: Int, limit: Int) -> [Bool]? {
        guard limit > 0, limit <= maxSegments else { return nil }
        let filled = min(max(used, 0), limit)
        return (0..<limit).map { $0 < filled }
    }

    static func remainingText(used: Int, limit: Int) -> String {
        guard limit > 0 else { return "No automatic check-ins" }
        let left = max(limit - used, 0)
        return left == 0 ? "All used this week" : "\(left) left this week"
    }
}

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

/// Small hint under a disabled action ("Needs a connection").
struct OfflineHint: View {
    let text: String
    var body: some View {
        Label(text, systemImage: "icloud.slash")
            .font(.footnote)
            .foregroundStyle(.secondary)
    }
}
