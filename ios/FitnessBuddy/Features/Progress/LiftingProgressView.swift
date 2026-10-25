import Charts
import SwiftData
import SwiftUI

struct LiftingProgressView: View {
    @Environment(AppEnvironment.self) private var env
    @Query private var planned: [CachedPlannedSession]
    @State private var range: SessionRange = .eightWeeks
    @State private var showAllLifts = false

    private var lifts: StatsResource<LiftsStatsDTO> { env.stats.resource(StatsEndpoints.lifts) }
    private var volume: StatsResource<WeeklyVolumeDTO> { env.stats.resource(StatsEndpoints.weeklyVolume()) }

    /// Lifting sessions planned for the current week in the cached plan (fallback when the stats carry no plan figure).
    private var plannedThisWeek: Int? {
        let days = Set(AppClock.weekDays(containing: AppClock.now))
        let n = planned.filter { days.contains($0.date) && WorkoutChooserLogic.liftingKinds.contains($0.kind) }.count
        return n > 0 ? n : nil
    }

    var body: some View {
        let lifts = lifts, volume = volume
        ScrollView {
            VStack(spacing: 16) {
                if lifts.value == nil && volume.value == nil {
                    StatsPlaceholder(resource: lifts, symbol: "dumbbell.fill", emptyTitle: "lifting stats")
                } else {
                    let all = lifts.value?.lifts ?? []
                    let weeks = volume.value?.weeks ?? []
                    LiftingKpiCard(kpis: LiftingDashboard.kpis(weeks: weeks, lifts: all, plannedThisWeekFallback: plannedThisWeek))
                    SessionsCard(series: LiftingDashboard.series(weeks, lifts: all, range: range), range: $range)
                    ProgramLiftsSection(lifts: all, showAll: { showAllLifts = true })
                }
                FreshnessLabel(resource: lifts)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .refreshable { await refreshStats(env, [lifts, volume]) }
        .loadsStats([lifts, volume])
        .sheet(isPresented: $showAllLifts) { AllLiftsSheet(lifts: lifts.value?.lifts ?? []) }
    }
}

// MARK: - KPI tiles

struct KpiTile: View {
    let title: String
    let value: String
    var unit: String? = nil
    var caption: String? = nil
    var captionColor: Color = .secondary
    var color: Color = .primary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption.weight(.medium)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value).metricStyle(.title).foregroundStyle(color)
                if let unit { Text(unit).font(.footnote.weight(.medium)).foregroundStyle(.secondary) }
            }
            if let caption {
                Text(caption).font(.caption.weight(.semibold)).foregroundStyle(captionColor).fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color.screenBackground.opacity(0.6), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// Two-column grid of tiles that collapses to one column at large text sizes.
struct KpiGrid<Content: View>: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    @ViewBuilder var content: Content
    var body: some View {
        let columns = typeSize.isAccessibilitySize
            ? [GridItem(.flexible(), alignment: .top)]
            : [GridItem(.adaptive(minimum: 150), spacing: 10, alignment: .top)]
        LazyVGrid(columns: columns, spacing: 10) { content }
    }
}

private struct LiftingKpiCard: View {
    let kpis: LiftingKpis

    var body: some View {
        let sessionsValue = kpis.sessionsPlanned.map { "\(kpis.sessionsDone)/\($0)" } ?? "\(kpis.sessionsDone)"
        Card(tint: Domain.lifting.color) {
            SectionLabel(title: "Training", symbol: "dumbbell.fill", color: Domain.lifting.color)
            KpiGrid {
                KpiTile(title: "Sessions this week", value: sessionsValue,
                        caption: kpis.sessionsPlanned == nil ? "no plan figure yet" : (kpis.sessionsDone >= (kpis.sessionsPlanned ?? 0) ? "on plan" : "to go: \((kpis.sessionsPlanned ?? 0) - kpis.sessionsDone)"),
                        captionColor: kpis.sessionsPlanned.map { kpis.sessionsDone >= $0 } == true ? .green : .secondary,
                        color: Domain.lifting.color)
                KpiTile(title: "PRs, last 30 days", value: "\(kpis.prsLast30Days)", caption: kpis.prsLast30Days == 1 ? "lift" : "lifts",
                        color: kpis.prsLast30Days > 0 ? Domain.lifting.color : .primary)
                KpiTile(title: "Lifts progressing", value: "\(kpis.progressing)", unit: "of \(kpis.tracked)",
                        caption: kpis.stalled > 0 ? "\(kpis.stalled) stalled" : "none stalled",
                        captionColor: kpis.stalled > 0 ? .orange : .green)
                KpiTile(title: "Consistency", value: kpis.consistencyPct.map { "\(Int($0.rounded()))%" } ?? "–",
                        caption: kpis.consistencyPct == nil ? "needs planned weeks" : "planned sessions, 8 weeks")
            }
        }
    }
}

// MARK: - Sessions vs plan, PRs

private struct SessionsCard: View {
    let series: [SessionsWeek]
    @Binding var range: SessionRange
    @State private var selected: Date?

    private var selectedWeek: SessionsWeek? {
        guard let selected else { return nil }
        return series.min { abs($0.date.timeIntervalSince(selected)) < abs($1.date.timeIntervalSince(selected)) }
    }

    private var hasPlan: Bool { series.contains { $0.planned != nil } }
    private var hasAnything: Bool { series.contains { $0.done > 0 || $0.prs > 0 || ($0.planned ?? 0) > 0 } }
    private var maxY: Int { max(3, series.map { max($0.done, $0.planned ?? 0) }.max() ?? 3) }

    var body: some View {
        ChartCard(title: "Sessions and PRs", subtitle: "Lifting sessions done per week against the plan. A star marks weeks with a PR.",
                  symbol: "chart.bar.fill", color: Domain.lifting.color) {
            Picker("Range", selection: $range) {
                ForEach(SessionRange.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            if !hasAnything {
                ChartEmpty(symbol: "chart.bar", message: "Log a workout and your weekly sessions show up here.")
            } else {
                legend
                Chart {
                    ForEach(series) { w in
                        if let p = w.planned {
                            BarMark(x: .value("Week", w.date, unit: .weekOfYear), yStart: .value("Zero", 0), yEnd: .value("Planned", p))
                                .foregroundStyle(Color.secondary.opacity(0.22))
                                .cornerRadius(3)
                        }
                        BarMark(x: .value("Week", w.date, unit: .weekOfYear), yStart: .value("Zero", 0), yEnd: .value("Done", w.done))
                            .foregroundStyle(Domain.lifting.color.gradient)
                            .cornerRadius(3)
                            .opacity(selectedWeek == nil || selectedWeek?.weekStart == w.weekStart ? 1 : 0.45)
                        if w.prs > 0 {
                            // PR weeks: a star (with the count while there is room) above the bar.
                            PointMark(x: .value("Week", w.date, unit: .weekOfYear), y: .value("Top", max(w.done, w.planned ?? 0)))
                                .opacity(0)
                                .annotation(position: .top, spacing: 2) {
                                    HStack(spacing: 1) {
                                        Image(systemName: "star.fill")
                                        if series.count <= 14 && w.prs > 1 { Text("\(w.prs)").font(.caption2.weight(.bold)) }
                                    }
                                    .font(.caption2).foregroundStyle(ChartPalette.pr)
                                    .accessibilityHidden(true)
                                }
                        }
                    }
                    if let s = selectedWeek {
                        RuleMark(x: .value("Week", s.date, unit: .weekOfYear))
                            .foregroundStyle(.secondary.opacity(0.4))
                            .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) { callout(s) }
                    }
                }
                .chartXSelection(value: $selected)
                .chartXAxis { xAxis }
                .chartYScale(domain: 0...(Double(maxY) + 1.6))
                .chartYAxis { AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { v in
                    AxisGridLine(); AxisValueLabel { if let d = v.as(Int.self) { Text("\(d)") } } } }
                .frame(height: 220)
                .accessibilityLabel("Lifting sessions per week, \(series.count) weeks")
                .accessibilityValue(summary)
            }
        }
    }

    @AxisContentBuilder private var xAxis: some AxisContent {
        switch range {
        case .eightWeeks:
            AxisMarks(values: .stride(by: .weekOfYear, count: 2)) { _ in
                AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated).day(), anchor: .top)
            }
        case .sixMonths:
            AxisMarks(values: .stride(by: .month)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated), anchor: .top) }
        case .year:
            AxisMarks(values: .stride(by: .month, count: 2)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated), anchor: .top) }
        }
    }

    private var legend: some View {
        HStack(spacing: 14) {
            key(Domain.lifting.color, "Done")
            if hasPlan { key(Color.secondary.opacity(0.35), "Planned") }
            HStack(spacing: 4) { Image(systemName: "star.fill").foregroundStyle(ChartPalette.pr); Text("PR week").foregroundStyle(.secondary) }
        }
        .font(.caption)
        .accessibilityHidden(true)
    }

    private func key(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 5) { RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 10, height: 10); Text(text).foregroundStyle(.secondary) }
    }

    private func callout(_ s: SessionsWeek) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Week of \(DayLabel.short(s.weekStart))").font(.caption.weight(.semibold))
            Text(s.planned.map { "\(s.done) of \($0) sessions" } ?? "\(s.done) sessions").font(.caption)
            if s.prs > 0 { Text("\(s.prs) with a PR").font(.caption).foregroundStyle(ChartPalette.pr) }
        }
        .padding(8).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }

    private var summary: String {
        let done = series.map(\.done).reduce(0, +)
        let prWeeks = series.filter { $0.prs > 0 }.count
        return "\(done) sessions, \(prWeeks) \(prWeeks == 1 ? "week" : "weeks") with a PR in this range"
    }
}

// MARK: - Program lifts

private struct ProgramLiftsSection: View {
    let lifts: [LiftSummaryDTO]
    let showAll: () -> Void

    var body: some View {
        let program = LiftProgram.inProgram(lifts)
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                SectionLabel(title: "Your program", symbol: "list.bullet", color: Domain.lifting.color)
                Spacer(minLength: 8)
                if !lifts.isEmpty {
                    Button(action: showAll) { Label("All lifts", systemImage: "magnifyingglass") }
                        .font(.subheadline.weight(.semibold))
                        .buttonStyle(.bordered).controlSize(.small).tint(Domain.lifting.color)
                }
            }
            if lifts.isEmpty {
                Card { ChartEmpty(symbol: "dumbbell", message: "No lifts yet. They appear after your first synced workout.") }
            } else if program.isEmpty {
                Card { ChartEmpty(symbol: "dumbbell", message: "No lifts in your current program. Open All lifts to browse your history.", height: 80) }
            } else {
                ForEach(LiftProgram.grouped(program), id: \.id) { group in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(group.region.rawValue).font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                            .textCase(.uppercase).padding(.leading, 4)
                            .accessibilityAddTraits(.isHeader)
                        LiftRows(rows: group.lifts)
                    }
                }
            }
        }
    }
}

struct LiftRows: View {
    let rows: [LiftSummaryDTO]
    var showGym = false

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.element.id) { i, lift in
                NavigationLink(value: lift) { LiftRow(lift: lift, showGym: showGym) }.buttonStyle(.plain)
                if i < rows.count - 1 { Divider().padding(.leading, 14) }
            }
        }
        .background(Color.cardBackground, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

/// Compact row: name on the left, current e1RM and 4-week trend on the right.
struct LiftRow: View {
    let lift: LiftSummaryDTO
    var showGym = false

    var body: some View {
        let trend = Trend(pct: lift.trend4wPct)
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(lift.name.withoutGymSuffix).font(.subheadline.weight(.medium)).lineLimit(3)
                if showGym, let gym = lift.gymName { Text(gym).font(.caption).foregroundStyle(.secondary) }
            }
            .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 2) {
                if let e = lift.latestE1rm {
                    Text("\(Fmt.number(e, maxFraction: 1)) kg").metricStyle(.subheadline, weight: .semibold)
                } else {
                    Text("No e1RM").font(.caption).foregroundStyle(.tertiary)
                }
                if lift.isStalled {
                    Text("Stalled").font(.caption.weight(.semibold)).foregroundStyle(.orange)
                } else if lift.trend4wPct != nil {
                    Label(Fmt.percent(lift.trend4wPct), systemImage: trend.symbol)
                        .font(.caption.weight(.semibold)).foregroundStyle(trendColor(trend))
                }
            }
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary).accessibilityHidden(true)
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(lift.name.withoutGymSuffix), estimated one rep max \(lift.latestE1rm.map { Fmt.number($0, maxFraction: 1) } ?? "unknown") kilograms, four week trend \(Fmt.percent(lift.trend4wPct))\(lift.isStalled ? ", stalled" : "")")
    }

    private func trendColor(_ t: Trend) -> Color {
        switch t {
        case .up: .green
        case .down: .orange
        default: .secondary
        }
    }
}

// MARK: - All lifts

private struct AllLiftsSheet: View {
    @Environment(\.dismiss) private var dismiss
    let lifts: [LiftSummaryDTO]
    @State private var query = ""
    @State private var region: BodyRegion?

    var body: some View {
        let regions = BodyRegion.allCases.filter { r in lifts.contains { LiftProgram.region(of: $0) == r } }
        let shown = LiftListing.filtered(lifts, query: query, muscle: nil)
            .filter { region == nil || LiftProgram.region(of: $0) == region }
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if regions.count > 1 {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                FilterChip(title: "All", selected: region == nil) { region = nil }
                                ForEach(regions) { r in FilterChip(title: r.rawValue, selected: region == r) { region = (region == r ? nil : r) } }
                            }
                        }
                    }
                    if shown.isEmpty {
                        Card { ChartEmpty(symbol: "magnifyingglass", message: "No lift matches that search.", height: 80) }
                    } else if query.isEmpty && region == nil {
                        ForEach(LiftProgram.grouped(shown), id: \.id) { group in
                            Text("\(group.region.rawValue) · \(group.lifts.count)").font(.footnote.weight(.semibold))
                                .foregroundStyle(.secondary).textCase(.uppercase).padding(.leading, 4)
                            LiftRows(rows: LiftListing.filtered(group.lifts, query: "", muscle: nil), showGym: true)
                        }
                    } else {
                        LiftRows(rows: shown, showGym: true)
                    }
                }
                .padding(16)
            }
            .background(Color.screenBackground)
            .navigationTitle("All lifts")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search lifts")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .navigationDestination(for: LiftSummaryDTO.self) { LiftDetailView(lift: $0) }
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
