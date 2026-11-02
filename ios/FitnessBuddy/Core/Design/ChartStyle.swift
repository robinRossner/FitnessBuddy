import FitnessCore
import Charts
import SwiftUI

/// Shared look of the Progress / Health charts.
enum ChartPalette {
    /// Heart-rate zones Z1...Z5, cool to hot.
    static let zones: [Color] = [.cyan, .green, .yellow, .orange, .red]
    static func zone(_ z: Int) -> Color { zones[max(0, min(4, z - 1))] }

    // Health metrics use their `Domain` colour, so a chart matches its card and category header.
    static let hrv = Domain.heart.color
    static let rhr = Domain.heart.color
    static let sleep = Domain.sleep.color
    static let battery = Color.mint
    static let steps = Domain.activity.color
    static let weight = Domain.body.color
    /// Personal records: a warm gold that stays readable on light and dark backgrounds.
    static let pr = Color(red: 0.93, green: 0.68, blue: 0.0)

    /// Run types in the weekly distance chart.
    static func runType(_ type: RunType) -> Color {
        switch type {
        case .easy: .green
        case .intervals: .orange
        case .long: .blue
        case .other: .gray
        }
    }

    static func lapKind(_ kind: LapKind) -> Color {
        switch kind {
        case .work: Domain.running.color
        case .warmup, .cooldown: .gray
        case .recovery: .blue
        case .other: .secondary
        }
    }
}

/// A titled card that holds one chart (or list).
struct ChartCard<Content: View, Trailing: View>: View {
    let title: String
    var subtitle: String? = nil
    var symbol: String? = nil
    var color: Color = .secondary
    @ViewBuilder var trailing: Trailing
    @ViewBuilder var content: Content

    var body: some View {
        Card {
            HStack(alignment: .firstTextBaseline) {
                SectionLabel(title: title, symbol: symbol, color: color)
                Spacer(minLength: 8)
                trailing
            }
            if let subtitle {
                Text(subtitle).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            content
        }
    }
}

extension ChartCard where Trailing == EmptyView {
    init(title: String, subtitle: String? = nil, symbol: String? = nil, color: Color = .secondary,
         @ViewBuilder content: () -> Content) {
        self.init(title: title, subtitle: subtitle, symbol: symbol, color: color, trailing: { EmptyView() }, content: content)
    }
}

/// An empty chart that still looks designed: icon, a line of explanation, fixed height so the layout does not jump.
struct ChartEmpty: View {
    let symbol: String
    let message: String
    var height: CGFloat = 140

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: symbol).font(.title2).foregroundStyle(.tertiary)
            Text(message).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, minHeight: height)
        .accessibilityElement(children: .combine)
    }
}

/// Small grey note under a chart, e.g. "Garmin data since Aug 2026".
struct ChartNote: View {
    let text: String
    var symbol: String = "info.circle"
    var body: some View {
        Label(text, systemImage: symbol).font(.caption).foregroundStyle(.secondary)
    }
}

// MARK: - Loading states

/// Loads all resources concurrently (they are main-actor objects; the network work happens off the main thread).
@MainActor
func loadStats(_ resources: [any StatsLoadable], generation: Int, maxAge: TimeInterval, force: Bool) async {
    let tasks = resources.map { r in Task { @MainActor in await r.load(generation: generation, maxAge: maxAge, force: force, followsSync: true) } }
    for t in tasks { await t.value }
}

/// Loads the given stats resources when the view appears (cache first, refetch if stale) and again after every sync.
private struct StatsLoaderModifier: ViewModifier {
    @Environment(AppEnvironment.self) private var env
    let resources: [any StatsLoadable]

    func body(content: Content) -> some View {
        content.task(id: env.sync.generation) {
            await loadStats(resources, generation: env.sync.generation, maxAge: StatsFreshness.maxAge, force: false)
        }
    }
}

extension View {
    func loadsStats(_ resources: [any StatsLoadable]) -> some View { modifier(StatsLoaderModifier(resources: resources)) }
}

/// Pull-to-refresh helper: sync first (pushes local changes, pulls), then force-refetch the stats.
@MainActor
func refreshStats(_ env: AppEnvironment, _ resources: [any StatsLoadable]) async {
    await env.sync.sync(.pullToRefresh)
    await loadStats(resources, generation: env.sync.generation, maxAge: 0, force: true)
}

/// "Updated 2 h ago" / "Offline, showing data from ..." + spinner while refreshing.
struct FreshnessLabel<R: Decodable & Sendable>: View {
    let resource: StatsResource<R>

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            HStack(spacing: 6) {
                if resource.isRefreshing { ProgressView().controlSize(.mini) }
                else if resource.isOffline { Image(systemName: "icloud.slash") }
                if let text = text(now: context.date) { Text(text) }
            }
            .font(.caption)
            .foregroundStyle(resource.failure != nil && !resource.isOffline ? Color.orange : Color.secondary)
            .frame(maxWidth: .infinity, alignment: .center)
            .accessibilityElement(children: .combine)
        }
    }

    private func text(now: Date) -> String? {
        if let failure = resource.failure, !resource.isOffline {
            return "Couldn't refresh: \(failure)"
        }
        return StatsFreshness.label(fetchedAt: resource.fetchedAt, offline: resource.isOffline, now: now)
    }
}
