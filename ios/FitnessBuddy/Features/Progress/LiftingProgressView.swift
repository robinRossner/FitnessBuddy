import FitnessCore
import Charts
import SwiftData
import SwiftUI

/// Lifting segment of Progress: stat tiles, sessions per week (PR stars, plan line) and one card per program lift.
/// Content only; `ProgressTabView` owns the scroll view and the navigation destinations.
struct LiftingProgressView: View {
    @Environment(AppEnvironment.self) private var env
    @Query private var planned: [CachedPlannedSession]
    @Query(filter: #Predicate<LocalSession> { $0.endedAt != nil && $0.deletedAt == nil }) private var finished: [LocalSession]
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
        VStack(alignment: .leading, spacing: Kit.groupSpacing) {
            if lifts.value == nil && volume.value == nil {
                StatsPlaceholder(resource: lifts, symbol: "dumbbell.fill", emptyTitle: "lifting stats")
            } else {
                let all = lifts.value?.lifts ?? []
                let weeks = volume.value?.weeks ?? []
                LiftingTiles(kpis: LiftingDashboard.kpis(weeks: weeks, lifts: all, plannedThisWeekFallback: plannedThisWeek))
                SessionsCard(series: LiftingDashboard.series(weeks, lifts: all, range: .eightWeeks))
                ProgramLiftsSection(lifts: all, target: target(for:), showAll: { showAllLifts = true })
            }
            FreshnessLabel(resource: lifts)
        }
        .loadsStats([lifts, volume])
        .sheet(isPresented: $showAllLifts) { AllLiftsSheet(lifts: lifts.value?.lifts ?? []) }
    }

    private func target(for lift: LiftSummaryDTO) -> NextLiftTarget? {
        ProgressModel.nextTarget(variantId: lift.variantId, items: planned.compactMap(\.item),
                                 doneIds: Set(finished.compactMap(\.plannedSessionId)), today: AppClock.todayString)
    }
}

// MARK: - KPI tiles

/// Older tile (Health still uses it); new screens use the kit's `MetricTile`.
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
    var spacing: CGFloat = 10
    @ViewBuilder var content: Content
    var body: some View {
        let columns = typeSize.isAccessibilitySize
            ? [GridItem(.flexible(), alignment: .top)]
            : [GridItem(.adaptive(minimum: 150), spacing: spacing, alignment: .top)]
        LazyVGrid(columns: columns, spacing: spacing) { content }
    }
}

private struct LiftingTiles: View {
    let kpis: LiftingKpis

    var body: some View {
        let c = Domain.lifting.color
        KpiGrid(spacing: 12) {
            MetricTile(label: "This Week", symbol: "calendar", color: c, value: "\(kpis.sessionsDone)",
                       unit: kpis.sessionsPlanned.map { "of \($0)" } ?? (kpis.sessionsDone == 1 ? "session" : "sessions"),
                       info: kpis.sessionsPlanned == nil ? "Lifting sessions finished this week. No plan figure for this week yet."
                                                         : "Lifting sessions finished this week against the plan.")
            MetricTile(label: "PRs, 30 Days", symbol: "star.fill", color: c, value: "\(kpis.prsLast30Days)",
                       unit: kpis.prsLast30Days == 1 ? "lift" : "lifts",
                       info: "Lifts that set a personal record in the last 30 days.")
            MetricTile(label: "Progressing", symbol: "arrow.up.right", color: c, value: "\(kpis.progressing)",
                       unit: "of \(kpis.tracked)",
                       info: "Program lifts whose estimated 1RM went up over the last 4 weeks. "
                           + (kpis.stalled > 0 ? "\(kpis.stalled) \(kpis.stalled == 1 ? "has" : "have") stalled." : "None has stalled."))
            MetricTile(label: "Consistency", symbol: "checkmark.seal", color: c,
                       value: kpis.consistencyPct.map { "\(Int($0.rounded()))" } ?? "–", unit: kpis.consistencyPct == nil ? nil : "%",
                       info: kpis.consistencyPct == nil ? "Share of planned lifting sessions done over the last 8 weeks. Needs a few planned weeks first."
                                                        : "Share of planned lifting sessions done over the last 8 weeks.")
        }
    }
}

// MARK: - Sessions per week

private struct SessionsCard: View {
    let series: [SessionsWeek]
    @State private var selectedKey: String?

    private let c = Domain.lifting.color
    private var hasPlan: Bool { series.contains { $0.planned != nil } }
    private var hasAnything: Bool { series.contains { $0.done > 0 || $0.prs > 0 || ($0.planned ?? 0) > 0 } }
    private var maxY: Int { max(3, series.map { max($0.done, $0.planned ?? 0) }.max() ?? 3) }
    private var selected: SessionsWeek? { selectedKey.flatMap { k in series.first { $0.weekStart == k } } }

    var body: some View {
        Card {
            CardHeader(title: "Sessions per Week", symbol: "dumbbell.fill", color: c, time: "\(series.count) weeks",
                       info: "Lifting sessions done per week. The dashed line is your plan; a star marks a week with a personal record.")
            if !hasAnything {
                ChartEmpty(symbol: "chart.bar", message: "Log a workout and your weekly sessions show up here.")
            } else {
                chart.frame(height: 190)
                    .padding(.top, selected == nil ? 0 : 44)
                    .accessibilityLabel("Lifting sessions per week, \(series.count) weeks")
                    .accessibilityValue(summary)
                HStack(spacing: 14) {
                    legend(Image(systemName: "star.fill"), "PR week", c)
                    if hasPlan { legend(Image(systemName: "line.horizontal.3"), "Plan", .secondary) }
                }
                .accessibilityHidden(true)
            }
            ShowAllLink { LiveMetricDetailView(metric: MetricCatalog.liftingSessions) }
        }
    }

    private var chart: some View {
        Chart {
            ForEach(series) { w in
                BarMark(x: .value("Week", w.weekStart), y: .value("Sessions", w.done), width: .ratio(0.6))
                    .foregroundStyle(w.prs > 0 ? AnyShapeStyle(c.gradient) : AnyShapeStyle(c.opacity(0.55).gradient))
                    .cornerRadius(5)
                    .opacity(selected == nil || selected?.weekStart == w.weekStart ? 1 : 0.45)
                    .annotation(position: .top, spacing: 3) {
                        if w.prs > 0 {
                            Image(systemName: "star.fill").font(.caption2).foregroundStyle(c).accessibilityHidden(true)
                        }
                    }
                if let p = w.planned {
                    LineMark(x: .value("Week", w.weekStart), y: .value("Plan", p), series: .value("Series", "Plan"))
                        .interpolationMethod(.stepCenter)
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                        .foregroundStyle(Color.secondary)
                }
            }
            if let s = selected {
                RuleMark(x: .value("Week", s.weekStart))
                    .foregroundStyle(Color.secondary.opacity(0.3))
                    .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) { callout(s) }
            }
        }
        .chartXSelection(value: $selectedKey)
        .chartYScale(domain: 0...(Double(maxY) + 0.6))
        .chartYAxis {
            AxisMarks(values: Array(0...maxY).map(Double.init)) { _ in
                AxisGridLine().foregroundStyle(.quaternary)
                AxisValueLabel()
            }
        }
        .chartXAxis { progressWeekAxis(series.map(\.weekStart)) }
    }

    private func legend(_ image: Image, _ text: String, _ color: Color) -> some View {
        HStack(spacing: 4) { image.foregroundStyle(color); Text(text).foregroundStyle(.secondary) }.font(.caption)
    }

    private func callout(_ s: SessionsWeek) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Week of \(DayLabel.short(s.weekStart))").font(.caption.weight(.semibold))
            Text(s.planned.map { "\(s.done) of \($0) sessions" } ?? "\(s.done) \(s.done == 1 ? "session" : "sessions")").font(.caption)
            if s.prs > 0 { Text("\(s.prs) with a PR").font(.caption).foregroundStyle(c) }
        }
        .padding(8).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }

    private var summary: String {
        let done = series.map(\.done).reduce(0, +)
        let prWeeks = series.filter { $0.prs > 0 }.count
        return "\(done) sessions, \(prWeeks) \(prWeeks == 1 ? "week" : "weeks") with a PR"
    }
}

/// X axis for week bars keyed by `YYYY-MM-DD` week starts: every other week labelled with its date, the last as "Now".
func progressWeekAxis(_ keys: [String]) -> some AxisContent {
    let last = keys.last
    let shown = stride(from: keys.count - 1, through: 0, by: -2).map { keys[$0] }
    return AxisMarks(values: shown) { value in
        AxisValueLabel {
            if let key = value.as(String.self) {
                Text(key == last ? "Now" : DayLabel.short(key)).font(.caption2)
            }
        }
    }
}

// MARK: - Program lifts

private struct ProgramLiftsSection: View {
    let lifts: [LiftSummaryDTO]
    let target: (LiftSummaryDTO) -> NextLiftTarget?
    let showAll: () -> Void

    var body: some View {
        let program = LiftProgram.inProgram(lifts)
        PageSection("Lifts", info: "e1RM is your estimated one-rep max from the best set of a session (Epley). The target is what the progression engine planned for the next session.") {
            if lifts.isEmpty {
                Card { ChartEmpty(symbol: "dumbbell", message: "No lifts yet. They appear after your first synced workout.") }
            } else if program.isEmpty {
                Card { ChartEmpty(symbol: "dumbbell", message: "No lifts in your current program. Open All Lifts to browse your history.", height: 80) }
            } else {
                let groups = LiftProgram.grouped(program)
                ForEach(groups, id: \.id) { group in
                    if groups.count > 1 {
                        Text(group.region.rawValue).font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                            .textCase(.uppercase).padding(.leading, 4).padding(.top, 4)
                            .accessibilityAddTraits(.isHeader)
                    }
                    ForEach(group.lifts) { LiftProgressCard(lift: $0, target: target($0)) }
                }
            }
        } trailing: {
            if !lifts.isEmpty {
                Button("All Lifts", action: showAll).font(.body)
            }
        }
    }
}

/// One program lift: e1RM, 4-week trend, sparkline of the recent sessions and the next planned target; opens LiftDetailView.
private struct LiftProgressCard: View {
    @Environment(AppEnvironment.self) private var env
    let lift: LiftSummaryDTO
    let target: NextLiftTarget?

    private let c = Domain.lifting.color
    private var detail: StatsResource<LiftDetailDTO> { env.stats.resource(StatsEndpoints.liftDetail(lift.variantId)) }

    var body: some View {
        let detail = detail
        let spark = ProgressModel.e1rmSparkline(detail.value?.points ?? [])
        NavigationLink(value: lift) {
            Card {
                CardHeader(title: lift.name.withoutGymSuffix, symbol: "dumbbell.fill", color: c,
                           time: lift.isStalled ? "Stalled" : lift.lastDate.map { DayLabel.short($0) }, chevron: true)
                HStack(alignment: .bottom) {
                    VStack(alignment: .leading, spacing: 0) {
                        Text("e1RM").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            MetricText(value: lift.latestE1rm.map { Fmt.number($0, maxFraction: 1) } ?? "–", unit: "kg", size: .title)
                            if let t = lift.trend4wPct { TrendBadge(delta: t, higherIsBetter: true) }
                        }
                    }
                    Spacer(minLength: 8)
                    if spark.count >= 2 {
                        Sparkline(values: spark, color: lift.isStalled ? c.opacity(0.5) : c)
                            .frame(width: 110, height: 36)
                            .accessibilityHidden(true)
                    }
                }
                if let target {
                    Label {
                        Text("Next \(DayLabel.weekday(target.date)): \(target.text)")
                    } icon: {
                        Image(systemName: "target").foregroundStyle(c)
                    }
                    .font(.subheadline).foregroundStyle(.secondary)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibility)
        // A list decoration: the cached history is enough, refetch at most every few hours and not after each sync.
        .task(id: lift.variantId) {
            await detail.load(generation: env.sync.generation, maxAge: 6 * 3600, followsSync: false)
        }
    }

    private var accessibility: String {
        var s = "\(lift.name.withoutGymSuffix), estimated one rep max \(lift.latestE1rm.map { Fmt.number($0, maxFraction: 1) } ?? "unknown") kilograms"
        if let t = lift.trend4wPct { s += ", four week trend \(Fmt.percent(t))" }
        if lift.isStalled { s += ", stalled" }
        if let target { s += ", next \(DayLabel.weekday(target.date)) \(target.text)" }
        return s
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
