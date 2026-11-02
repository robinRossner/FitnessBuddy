import FitnessCore
import Charts
import SwiftUI

struct InsightsRoute: Hashable {}

/// Insights on their own screen (Summary's "Show All" pushes it): the same content as Progress' Insights segment.
struct InsightsView: View {
    @Environment(AppEnvironment.self) private var env

    var body: some View {
        ScrollView {
            InsightsContent().pagePadding()
        }
        .screenStyle()
        .navigationTitle("Insights")
        .navigationBarTitleDisplayMode(.large)
        .refreshable { await refreshStats(env, [env.stats.resource(StatsEndpoints.correlations())]) }
    }
}

/// What moves what: a summary card, then the real correlations grouped Recovery / Hybrid under coloured headers, each in
/// a card with its scatter that opens `InsightDetailView`. Items without enough data stay as one quiet line.
struct InsightsContent: View {
    @Environment(AppEnvironment.self) private var env

    private var correlations: StatsResource<CorrelationsDTO> { env.stats.resource(StatsEndpoints.correlations()) }

    var body: some View {
        let correlations = correlations
        VStack(alignment: .leading, spacing: Kit.groupSpacing) {
            if let items = correlations.value?.items {
                let groups = InsightTransforms.grouped(items)
                if groups.isEmpty {
                    Card { ChartEmpty(symbol: "lightbulb", message: "Not enough data yet. Patterns between sleep, HRV and training show up after a few weeks.") }
                } else {
                    InsightsSummaryCard(items: groups.flatMap(\.items))
                }
                ForEach(groups) { group in
                    let style = InsightGroupStyle(group.category)
                    VStack(alignment: .leading, spacing: 12) {
                        Label(group.category.title, systemImage: style.symbol)
                            .font(.title2.weight(.bold))
                            .foregroundStyle(style.color)
                            .accessibilityAddTraits(.isHeader)
                        ForEach(group.items) { item in
                            if InsightTransforms.hasEnoughData(item) {
                                NavigationLink {
                                    InsightDetailView(item: item)
                                } label: {
                                    Card { InsightCardContent(item: item, color: style.color, chartHeight: 150) }
                                }
                                .buttonStyle(.plain)
                                .accessibilityHint("Shows the correlation in detail")
                            } else {
                                CollapsedInsight(item: item)
                            }
                        }
                    }
                }
            } else {
                StatsPlaceholder(resource: correlations, symbol: "lightbulb", emptyTitle: "insights")
            }
            FreshnessLabel(resource: correlations)
        }
        .loadsStats([correlations])
    }
}

/// Header colour and symbol of a correlation group: recovery indigo, hybrid insights teal.
struct InsightGroupStyle {
    let color: Color
    let symbol: String

    init(_ category: CorrelationCategory) {
        switch category {
        case .recovery: color = Domain.recovery.color; symbol = Domain.recovery.symbol
        case .hybrid: color = Domain.insights.color; symbol = "arrow.triangle.merge"
        case .unknown: color = Domain.insights.color; symbol = Domain.insights.symbol
        }
    }
}

private struct InsightsSummaryCard: View {
    let items: [CorrelationDTO]

    var body: some View {
        let rated = items.filter(InsightTransforms.hasEnoughData)
        let clear = rated.filter { $0.strength >= 2 }.count
        Card {
            CardHeader(title: "Correlations", symbol: Domain.insights.symbol, color: Domain.insights.color, time: "Last 120 days",
                       info: "Each card compares two of your own measurements day by day. r is the correlation (−1 to 1), n the number of days. Few days means an early read, not a rule, and a link is not proof that one thing causes the other.")
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                MetricText(value: "\(items.count)", size: .title)
                Text("\(items.count == 1 ? "link" : "links") checked · \(clear) moderate or strong")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct CollapsedInsight: View {
    let item: CorrelationDTO

    var body: some View {
        Card(padding: 14) {
            HStack(spacing: 10) {
                Image(systemName: "chart.dots.scatter").foregroundStyle(.tertiary).accessibilityHidden(true)
                Text(item.title).font(.subheadline.weight(.medium)).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Text("Needs more data").font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.trailing)
            }
        }
        .accessibilityElement(children: .combine)
    }
}
