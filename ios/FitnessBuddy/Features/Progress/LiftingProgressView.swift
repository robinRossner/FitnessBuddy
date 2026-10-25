import Charts
import SwiftUI

struct LiftingProgressView: View {
    @Environment(AppEnvironment.self) private var env
    @State private var query = ""
    @State private var muscle: String?
    @State private var showInactive = false

    private var lifts: StatsResource<LiftsStatsDTO> { env.stats.resource(StatsEndpoints.lifts) }
    private var volume: StatsResource<WeeklyVolumeDTO> { env.stats.resource(StatsEndpoints.weeklyVolume()) }

    var body: some View {
        let lifts = lifts, volume = volume
        ScrollView {
            VStack(spacing: 16) {
                if lifts.value == nil && volume.value == nil {
                    StatsPlaceholder(resource: lifts, symbol: "dumbbell.fill", emptyTitle: "lifting stats")
                } else {
                    SummaryCard(lifts: lifts.value?.lifts ?? [], weeks: volume.value?.weeks ?? [])
                    WeeklyVolumeCard(weeks: volume.value?.weeks ?? [])
                    LiftListSection(lifts: lifts.value?.lifts ?? [], query: query, muscle: $muscle, showInactive: $showInactive)
                }
                FreshnessLabel(resource: lifts)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .automatic), prompt: "Search lifts")
        .refreshable { await refreshStats(env, [lifts, volume]) }
        .loadsStats([lifts, volume])
    }
}

// MARK: - Summary tiles

private struct SummaryCard: View {
    let lifts: [LiftSummaryDTO]
    let weeks: [WeekVolumeDTO]

    var body: some View {
        let week = VolumeTransforms.thisWeek(weeks)
        let prs = LiftListing.recentPRCount(lifts)
        Card(tint: Domain.lifting.color) {
            SectionLabel(title: "This week", symbol: "dumbbell.fill", color: Domain.lifting.color)
            HStack(alignment: .top, spacing: 12) {
                StatTile(title: "Sets", value: "\(week.sets)")
                StatTile(title: "Volume", value: week.volumeKg >= 10_000 ? Fmt.number(week.volumeKg / 1000, maxFraction: 1) : Fmt.number(week.volumeKg.rounded(), maxFraction: 0),
                         unit: week.volumeKg >= 10_000 ? "t" : "kg")
                StatTile(title: "PRs, 30 days", value: "\(prs)", color: prs > 0 ? Domain.lifting.color : .primary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Weekly volume

struct WeeklyVolumeCard: View {
    let weeks: [WeekVolumeDTO]
    @State private var selected: Date?

    private var bars: [(date: Date, week: WeekVolumeDTO)] {
        weeks.sorted { $0.weekStart < $1.weekStart }.compactMap { w in DayKey.date(w.weekStart).map { ($0, w) } }
    }

    private var selectedWeek: (date: Date, week: WeekVolumeDTO)? {
        guard let selected else { return nil }
        return bars.min { abs($0.date.timeIntervalSince(selected)) < abs($1.date.timeIntervalSince(selected)) }
    }

    var body: some View {
        ChartCard(title: "Weekly volume", subtitle: "Last 26 weeks, total weight moved", symbol: "chart.bar.fill", color: Domain.lifting.color) {
            if bars.allSatisfy({ ($0.week.liftingVolumeKg ?? 0) == 0 }) {
                ChartEmpty(symbol: "chart.bar", message: "Log a workout and your weekly volume shows up here.")
            } else {
                Chart {
                    ForEach(bars, id: \.week.weekStart) { item in
                        BarMark(x: .value("Week", item.date, unit: .weekOfYear), y: .value("Volume (t)", (item.week.liftingVolumeKg ?? 0) / 1000))
                            .foregroundStyle(Domain.lifting.color.gradient)
                            .opacity(selectedWeek == nil || selectedWeek?.week.weekStart == item.week.weekStart ? 1 : 0.4)
                            .cornerRadius(3)
                    }
                    if let s = selectedWeek {
                        RuleMark(x: .value("Week", s.date, unit: .weekOfYear))
                            .foregroundStyle(.secondary.opacity(0.4))
                            .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("Week of \(DayLabel.short(s.week.weekStart))").font(.caption.weight(.semibold))
                                    Text("\(Fmt.volume(s.week.liftingVolumeKg ?? 0)), \(s.week.liftingSets ?? 0) sets").font(.caption)
                                }
                                .padding(8).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
                            }
                    }
                }
                .chartXSelection(value: $selected)
                .chartXAxis { AxisMarks(values: .stride(by: .month, count: 2)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated)) } }
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text("\(Fmt.number(d, maxFraction: 0)) t") } } } }
                .frame(height: 180)
                .accessibilityLabel("Weekly lifting volume, last \(bars.count) weeks")
                .accessibilityValue(summary)
            }
        }
    }

    private var summary: String {
        guard let last = bars.last else { return "" }
        let avg = bars.map { ($0.week.liftingVolumeKg ?? 0) }.reduce(0, +) / Double(max(1, bars.count))
        return "Latest week \(Fmt.volume(last.week.liftingVolumeKg ?? 0)), average \(Fmt.volume(avg))"
    }
}

// MARK: - Lift list

private struct LiftListSection: View {
    let lifts: [LiftSummaryDTO]
    let query: String
    @Binding var muscle: String?
    @Binding var showInactive: Bool

    var body: some View {
        let inactiveCount = lifts.filter { !$0.isActive }.count
        // A search always looks through the whole history.
        let pool = (showInactive || !query.isEmpty) ? lifts : lifts.filter(\.isActive)
        let filtered = LiftListing.filtered(pool, query: query, muscle: muscle)
        let groups = LiftListing.muscleGroups(pool)
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(title: "Lifts", symbol: "list.bullet", color: Domain.lifting.color)
            if groups.count > 1 {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        FilterChip(title: "All", selected: muscle == nil) { muscle = nil }
                        ForEach(groups, id: \.self) { g in
                            FilterChip(title: MuscleGroup.display(g), selected: muscle == g) { muscle = (muscle == g ? nil : g) }
                        }
                    }
                }
            }
            if inactiveCount > 0 && query.isEmpty {
                Toggle("Show \(inactiveCount) inactive lifts", isOn: $showInactive)
                    .font(.subheadline).tint(Domain.lifting.color)
            }
            if lifts.isEmpty {
                Card { ChartEmpty(symbol: "dumbbell", message: "No lifts yet. They appear after your first synced workout.") }
            } else if filtered.isEmpty {
                Card { ChartEmpty(symbol: "magnifyingglass", message: "No lift matches that search.", height: 80) }
            } else if muscle == nil && query.isEmpty && groups.count > 1 {
                ForEach(groups + (filtered.contains { $0.muscleGroup == nil } ? [""] : []), id: \.self) { g in
                    let rows = filtered.filter { ($0.muscleGroup ?? "") == g }
                    if !rows.isEmpty {
                        Text(MuscleGroup.display(g.isEmpty ? nil : g)).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary).padding(.top, 4)
                        LiftRows(rows: rows)
                    }
                }
            } else {
                LiftRows(rows: filtered)
            }
        }
    }
}

private struct FilterChip: View {
    let title: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title).font(.subheadline.weight(.medium))
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(selected ? Domain.lifting.color : Color.cardBackground, in: Capsule())
                .foregroundStyle(selected ? Color.white : Color.primary)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

private struct LiftRows: View {
    let rows: [LiftSummaryDTO]

    var body: some View {
        LazyVStack(spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.element.id) { i, lift in
                NavigationLink(value: lift) { LiftRow(lift: lift) }.buttonStyle(.plain)
                if i < rows.count - 1 { Divider().padding(.leading, 16) }
            }
        }
        .background(Color.cardBackground, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
    }
}

private struct LiftRow: View {
    @Environment(AppEnvironment.self) private var env
    let lift: LiftSummaryDTO

    private var detail: StatsResource<LiftDetailDTO> { env.stats.resource(StatsEndpoints.liftDetail(lift.variantId)) }

    var body: some View {
        let trend = Trend(pct: lift.trend4wPct)
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(lift.name.withoutGymSuffix).font(.subheadline.weight(.medium)).lineLimit(2)
                if !subtitle.isEmpty { Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                if lift.isStalled { Chip(text: "Stalled", color: .orange) }
            }
            Spacer(minLength: 4)
            Sparkline(values: sparkValues, color: lift.isStalled ? .orange : Domain.lifting.color)
                .frame(width: 52, height: 26)
            VStack(alignment: .trailing, spacing: 2) {
                if let e = lift.latestE1rm {
                    HStack(alignment: .firstTextBaseline, spacing: 2) {
                        Text(Fmt.number(e, maxFraction: 1)).metricStyle(.headline)
                        Text("kg").font(.caption2).foregroundStyle(.secondary)
                    }
                    if lift.trend4wPct != nil {
                        HStack(spacing: 3) {
                            Image(systemName: trend.symbol)
                            Text(Fmt.percent(lift.trend4wPct))
                        }
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(trendColor(trend))
                    }
                } else {
                    Text("No e1RM").font(.caption).foregroundStyle(.tertiary)
                }
            }
            .frame(minWidth: 62, alignment: .trailing)
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(lift.name.withoutGymSuffix), estimated one rep max \(lift.latestE1rm.map { Fmt.number($0, maxFraction: 1) } ?? "unknown") kilograms, four week trend \(Fmt.percent(lift.trend4wPct))\(lift.isStalled ? ", stalled" : "")")
        .task { await detail.load(generation: env.sync.generation, maxAge: 24 * 3600, force: false, followsSync: false) }
    }

    private var subtitle: String {
        var parts: [String] = []
        if let g = lift.gymName { parts.append(g) }
        if let n = lift.sessions { parts.append("\(n) sessions") }
        if !lift.isActive { parts.append("inactive") }
        return parts.joined(separator: " · ")
    }

    private var sparkValues: [Double] {
        guard let pts = detail.value?.points else { return [] }
        return pts.sorted { $0.date < $1.date }.compactMap(\.bestE1rm).suffix(12).map { $0 }
    }

    private func trendColor(_ t: Trend) -> Color {
        switch t {
        case .up: .green
        case .down: .orange
        default: .secondary
        }
    }
}
