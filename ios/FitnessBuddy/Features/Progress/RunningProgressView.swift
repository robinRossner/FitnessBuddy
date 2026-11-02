import FitnessCore
import Charts
import SwiftData
import SwiftUI

/// Pushed screens of the Running segment (also reachable with the `-uiSheet vdot|efficiency` launch argument).
enum RunningRoute: Hashable {
    case vdot, efficiency, allRuns
}

/// Running segment of Progress: tiles, distance per week, this week's runs, fitness (VDOT, aerobic efficiency),
/// heart-rate zones and recent runs. Content only; `ProgressTabView` owns the scroll view and the destinations.
struct RunningProgressView: View {
    @Environment(AppEnvironment.self) private var env
    @Query private var planned: [CachedPlannedSession]
    @State private var openPlanned: PlannedRunSheet?

    private var running: StatsResource<RunningStatsDTO> { env.stats.resource(StatsEndpoints.running()) }
    private var recent: StatsResource<[RunSummaryDTO]> { env.stats.resource(StatsEndpoints.recentRuns()) }

    /// Runs planned for the current week in the cached plan (fallback when the stats carry no plan figure).
    private var plannedRunsThisWeek: Int? {
        let days = Set(AppClock.weekDays(containing: AppClock.now))
        let n = planned.filter { days.contains($0.date) && Domain.forKind($0.kind) == .running }.count
        return n > 0 ? n : nil
    }

    var body: some View {
        let running = running, recent = recent
        let items = planned.compactMap(\.item)
        let days = AppClock.weekDays(containing: AppClock.now)
        let weekRows = WeekRunsModel.build(days: days, items: items, runs: recent.value ?? [], finished: [],
                                           today: AppClock.todayString).rows
        let weekRuns = ProgressModel.weekRuns(runs: recent.value ?? [], planned: weekRows, days: days)
        VStack(alignment: .leading, spacing: Kit.groupSpacing) {
            if let data = running.value {
                let kpis = RunStackTransforms.kpis(data, plannedRunsThisWeek: plannedRunsThisWeek)
                let overview = ProgressModel.runningOverview(data, kpis: kpis, plan: items)
                VStack(alignment: .leading, spacing: 12) {
                    RunningTiles(overview: overview, kpis: kpis)
                    DistanceCard(overview: overview)
                }
                WeekRunsSection(runs: weekRuns, plannedKm: overview.kmPlannedThisWeek, open: { openPlanned = PlannedRunSheet(item: $0) })
                FitnessSection(data: data, kpis: kpis)
                ZonesCard(weeks: Array(RunTransforms.weeksWithRuns(data.weekly).suffix(12)))
            } else {
                StatsPlaceholder(resource: running, symbol: "figure.run", emptyTitle: "running stats")
                WeekRunsSection(runs: weekRuns, plannedKm: nil, open: { openPlanned = PlannedRunSheet(item: $0) })
            }
            RecentRunsSection(runs: recent.value ?? [], loaded: recent.value != nil)
            FreshnessLabel(resource: running)
        }
        .loadsStats([running, recent])
        .sheet(item: $openPlanned) { RunDetailSheet(item: $0.item) }
    }
}

private struct PlannedRunSheet: Identifiable {
    let item: PlanItemDTO
    var id: String { item.id ?? "\(item.date)-\(item.kind)" }
}

// MARK: - Tiles

private func changeColor(_ delta: Double?, threshold: Double = 0.05) -> Color {
    guard let delta else { return .secondary }
    if abs(delta) <= threshold { return .secondary }
    return delta > 0 ? .green : .orange
}

private func changeSymbol(_ delta: Double?, threshold: Double = 0.05) -> String {
    guard let delta, abs(delta) > threshold else { return "arrow.right" }
    return delta > 0 ? "arrow.up.right" : "arrow.down.right"
}

private struct RunningTiles: View {
    let overview: RunningOverview
    let kpis: RunKpiValues

    var body: some View {
        let c = Domain.running.color
        KpiGrid(spacing: 12) {
            MetricTile(label: "This Week", symbol: "calendar", color: c, value: Fmt.km(overview.kmThisWeek),
                       unit: overview.kmPlannedThisWeek.map { "of \(Fmt.km($0)) km" } ?? "km",
                       info: "Kilometres run this week (Monday to Sunday): \(kpis.runsThisWeek) "
                           + (kpis.runsPlanned.map { "of \($0) planned runs." } ?? (kpis.runsThisWeek == 1 ? "run." : "runs.")))
            MetricTile(label: "Last Week", symbol: "figure.run", color: c,
                       value: overview.kmLastWeek.map(Fmt.km) ?? "–", unit: overview.kmLastWeek == nil ? nil : "km")
            MetricTile(label: "Easy Pace", symbol: "speedometer", color: c,
                       value: overview.easyPaceSPerKm.map(Fmt.pace) ?? "–", unit: overview.easyPaceSPerKm == nil ? nil : "/km",
                       info: overview.easyRuns == 0
                           ? "Average pace of your easy runs over the last 4 weeks. No easy run with heart rate in that time."
                           : "Average pace of your \(overview.easyRuns) easy \(overview.easyRuns == 1 ? "run" : "runs") over the last 4 weeks, weighted by distance.")
            MetricTile(label: "VDOT", symbol: "bolt.fill", color: c,
                       value: kpis.vdotCurrent.map { Fmt.number($0, maxFraction: 2) } ?? "–",
                       info: "Jack Daniels' running fitness score. Your plan's training paces (easy, threshold, intervals) come from this number. The trend is under Fitness.")
        }
    }
}

// MARK: - Distance per week

private struct DistanceCard: View {
    let overview: RunningOverview
    @State private var selectedKey: String?

    private let c = Domain.running.color
    private var selected: ProgressWeekBar? { selectedKey.flatMap { k in overview.weeks.first { $0.key == k } } }

    var body: some View {
        let weeks = overview.weeks
        Card {
            CardHeader(title: "Distance per Week", symbol: "figure.run", color: c, time: "\(weeks.count) weeks",
                       info: "Kilometres run per week (Monday to Sunday). The dashed line is the average of the \(max(0, weeks.count - 1)) full weeks before this one.")
            if !weeks.contains(where: { $0.value > 0 }) {
                ChartEmpty(symbol: "figure.run", message: "No runs in these weeks. Your Garmin runs show up here.")
            } else {
                chart(weeks)
                    .frame(height: 190)
                    .padding(.top, selected == nil ? 0 : 30)
                    .accessibilityLabel("Running distance per week, \(weeks.count) weeks")
                    .accessibilityValue("This week \(Fmt.km(weeks.last?.value ?? 0)) kilometres"
                                        + (overview.averageKm.map { ", average \(Fmt.km($0)) kilometres" } ?? ""))
            }
            ShowAllLink { LiveMetricDetailView(metric: MetricCatalog.runningDistance) }
        }
    }

    private func chart(_ weeks: [ProgressWeekBar]) -> some View {
        Chart {
            ForEach(weeks) { w in
                BarMark(x: .value("Week", w.key), y: .value("km", w.value), width: .ratio(0.6))
                    .foregroundStyle(w.isCurrent ? AnyShapeStyle(c.gradient) : AnyShapeStyle(c.opacity(0.55).gradient))
                    .cornerRadius(5)
                    .opacity(selected == nil || selected?.key == w.key ? 1 : 0.45)
            }
            if let avg = overview.averageKm {
                RuleMark(y: .value("Average", avg))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                    .foregroundStyle(Color.secondary)
                    .annotation(position: .top, alignment: .leading) {
                        Text("Avg \(Fmt.km(avg)) km").font(.caption2).foregroundStyle(.secondary)
                    }
            }
            if let s = selected {
                RuleMark(x: .value("Week", s.key))
                    .foregroundStyle(Color.secondary.opacity(0.3))
                    .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Week of \(DayLabel.short(s.weekStart))").font(.caption.weight(.semibold))
                            Text("\(Fmt.km(s.value)) km").font(.caption.monospacedDigit())
                        }
                        .padding(8).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
                    }
            }
        }
        .chartXSelection(value: $selectedKey)
        .chartYAxis {
            AxisMarks { _ in
                AxisGridLine().foregroundStyle(.quaternary)
                AxisValueLabel()
            }
        }
        .chartXAxis { progressWeekAxis(weeks.map(\.key)) }
    }
}

// MARK: - This week's runs

private struct WeekRunsSection: View {
    let runs: [ProgressWeekRun]
    let plannedKm: Double?
    let open: (PlanItemDTO) -> Void

    var body: some View {
        if !runs.isEmpty {
            PageSection("This Week's Runs") {
                Card(padding: 0) {
                    VStack(spacing: 0) {
                        ForEach(Array(runs.enumerated()), id: \.element.id) { i, run in
                            if i > 0 { Divider().padding(.leading, 70) }
                            switch run.target {
                            case .run(let id):
                                NavigationLink(value: RunRoute(id: id)) { rowLink(run) }
                                    .buttonStyle(.plain)
                                    .accessibilityHint("Shows the run analysis")
                            case .planned(let item):
                                Button { open(item) } label: { rowLink(run) }
                                    .buttonStyle(.plain)
                                    .accessibilityHint("Shows the planned session")
                            }
                        }
                    }
                }
            } trailing: {
                if let plannedKm {
                    Text("\(Fmt.km(plannedKm)) km planned").font(.subheadline).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func rowLink(_ run: ProgressWeekRun) -> some View {
        HStack(spacing: 12) {
            WeekRunRowLabel(run: run)
            Spacer(minLength: 8)
            DisclosureChevron()
        }
        .padding(.vertical, 10).padding(.horizontal, 16)
        .contentShape(Rectangle())
    }
}

private struct WeekRunRowLabel: View {
    let run: ProgressWeekRun

    var body: some View {
        let c = Domain.running.color
        let isToday = run.date == AppClock.todayString
        HStack(spacing: 12) {
            Text(DayLabel.weekday(run.date))
                .font(.subheadline.weight(.bold))
                .foregroundStyle(isToday ? Color.white : c)
                .frame(width: 42, height: 42)
                .background(isToday ? AnyShapeStyle(c.gradient) : AnyShapeStyle(c.opacity(0.14)),
                            in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    Text(run.title).font(.body.weight(.medium)).lineLimit(1)
                    if run.isDone {
                        Chip(text: "Done", color: .green, symbol: "checkmark")
                    } else if let badge = run.badge {
                        Chip(text: badge, color: badge == "Today" ? c : (badge == "Missed" ? .orange : .secondary))
                    }
                }
                if !run.detail.isEmpty {
                    Text(run.detail).font(.subheadline).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.85)
                }
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(DayLabel.medium(run.date)), \(run.title)\(run.isDone ? ", done" : run.badge.map { ", \($0)" } ?? ""), \(run.detail)")
    }
}

// MARK: - Fitness (VDOT, aerobic efficiency)

private struct FitnessSection: View {
    let data: RunningStatsDTO
    let kpis: RunKpiValues

    var body: some View {
        let c = Domain.running.color
        let vdot = data.vdot.sorted { $0.date < $1.date }.compactMap(\.value)
        let efficiency = data.efficiency.sorted { $0.date < $1.date }.compactMap(\.efficiency)
        PageSection("Fitness", info: "How your running fitness develops. VDOT is the number your plan's paces come from; it moves after races, hard efforts and interval sessions. Aerobic efficiency moves after easy and long runs.") {
            MetricCard(title: "VDOT", symbol: "bolt.fill", color: c,
                       value: kpis.vdotCurrent.map { Fmt.number($0, maxFraction: 2) } ?? "–", unit: nil,
                       time: kpis.vdotChange.map { d in abs(d) < 0.05 ? "Unchanged in 4 weeks" : "\(d > 0 ? "+" : "")\(Fmt.number(d, maxFraction: 2)) in 4 weeks" }
                           ?? (kpis.vdotCurrent == nil ? "Set with your first plan" : "Plan VDOT"),
                       sparkline: Array(vdot.suffix(12))) {
                RunningRouteView(route: .vdot)
            }
            MetricCard(title: "Aerobic Efficiency", symbol: "lungs.fill", color: c,
                       value: kpis.efficiencyCurrent.map { Fmt.number($0, maxFraction: 2) } ?? "–", unit: nil,
                       time: kpis.efficiencyChangePct.map { "\(Fmt.percent($0)) in 4 weeks" }
                           ?? (kpis.efficiencyCurrent == nil ? "Needs easy runs" : nil),
                       sparkline: Array(efficiency.suffix(12))) {
                RunningRouteView(route: .efficiency)
            }
        }
    }
}

/// Resolves a `RunningRoute` with the cached running stats.
struct RunningRouteView: View {
    @Environment(AppEnvironment.self) private var env
    let route: RunningRoute

    private var running: StatsResource<RunningStatsDTO> { env.stats.resource(StatsEndpoints.running()) }

    var body: some View {
        switch route {
        case .allRuns:
            AllRunsView()
        case .vdot, .efficiency:
            let running = running
            Group {
                if let data = running.value {
                    let kpis = RunStackTransforms.kpis(data)
                    if route == .vdot { VdotDetailView(points: data.vdot, fit: data.vdotFit, kpis: kpis) } else { EfficiencyDetailView(points: data.efficiency, kpis: kpis) }
                } else {
                    ScrollView { StatsPlaceholder(resource: running, symbol: "figure.run", emptyTitle: "running stats").pagePadding() }
                        .screenStyle()
                }
            }
            .navigationTitle(route == .vdot ? "VDOT" : "Aerobic Efficiency")
            .refreshable { await refreshStats(env, [running]) }
            .loadsStats([running])
        }
    }
}

private struct VdotDetailView: View {
    let points: [VdotPointDTO]
    let fit: [VdotPointDTO]
    let kpis: RunKpiValues

    private static func series(_ points: [VdotPointDTO]) -> [(Date, Double)] {
        points.sorted { $0.date < $1.date }.compactMap { p -> (Date, Double)? in
            guard let d = DayKey.date(p.date), let v = p.value else { return nil }
            return (d, v)
        }
    }

    var body: some View {
        let c = Domain.running.color
        let items = Self.series(points)
        // The plan's VDOT holds until the next change: draw it on to today.
        let plan = items.last.map { last in last.0 < .now ? items + [(Date.now, last.1)] : items } ?? []
        // Older backends send a per-run fit as `vdot` and no `vdot_fit`: nothing to compare then.
        let fitItems = Self.series(fit)
        let lows = (plan + fitItems).map(\.1)
        let history = VdotSource.history(points)
        ScrollView {
            VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                Card {
                    CardHeader(title: "VDOT Trend", symbol: "bolt.fill", color: c, time: items.isEmpty ? nil : "\(items.count) \(items.count == 1 ? "value" : "updates")",
                               info: "The VDOT your plan uses. Your threshold, interval and easy paces are calculated from this number, so it is always the latest value in this chart.")
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        MetricText(value: kpis.vdotCurrent.map { Fmt.number($0, maxFraction: 2) } ?? "–", size: .largeTitle)
                        if let d = kpis.vdotChange {
                            Label("\(d > 0 ? "+" : "")\(Fmt.number(d, maxFraction: 2)) in 4 weeks", systemImage: changeSymbol(d))
                                .font(.footnote.weight(.semibold)).foregroundStyle(changeColor(d))
                        }
                    }
                    if items.isEmpty {
                        ChartEmpty(symbol: "speedometer", message: "Your plan's VDOT appears once your training plan has been created.", height: 100)
                    } else {
                        Chart {
                            ForEach(Array(plan.enumerated()), id: \.offset) { _, item in
                                AreaMark(x: .value("Date", item.0), yStart: .value("Low", (lows.min() ?? 0) - 1), yEnd: .value("VDOT", item.1), series: .value("Series", "Plan VDOT"))
                                    .interpolationMethod(.stepEnd)
                                    .foregroundStyle(LinearGradient(colors: [c.opacity(0.3), c.opacity(0.02)], startPoint: .top, endPoint: .bottom))
                                LineMark(x: .value("Date", item.0), y: .value("VDOT", item.1), series: .value("Series", "Plan VDOT"))
                                    .interpolationMethod(.stepEnd).foregroundStyle(c)
                                    .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round))
                            }
                            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                                PointMark(x: .value("Date", item.0), y: .value("VDOT", item.1)).foregroundStyle(c)
                            }
                            ForEach(Array(fitItems.enumerated()), id: \.offset) { _, item in
                                LineMark(x: .value("Date", item.0), y: .value("VDOT", item.1), series: .value("Series", "Race-fit estimate"))
                                    .interpolationMethod(.stepEnd).foregroundStyle(Color.secondary)
                                    .lineStyle(StrokeStyle(lineWidth: 1.5, lineCap: .round, dash: [4, 3]))
                            }
                        }
                        .chartYScale(domain: .automatic(includesZero: false))
                        .chartYAxis { AxisMarks { _ in AxisGridLine().foregroundStyle(.quaternary); AxisValueLabel() } }
                        .frame(height: 200)
                        .accessibilityLabel("Plan VDOT, \(items.count) values")
                        .accessibilityValue("Latest \(Fmt.number(items.last?.1 ?? 0, maxFraction: 2))")
                        HStack(spacing: 14) {
                            Label("Plan VDOT", systemImage: "circle.fill").foregroundStyle(c)
                            if !fitItems.isEmpty {
                                Label("Race-fit estimate", systemImage: "circle.dotted").foregroundStyle(.secondary)
                            }
                        }
                        .font(.caption).labelStyle(.titleAndIcon)
                    }
                }
                if !history.isEmpty {
                    PageSection("Changes") {
                        Card {
                            ForEach(Array(history.enumerated()), id: \.element.id) { i, p in
                                if i > 0 { Divider() }
                                VdotChangeRow(point: p)
                            }
                        }
                    }
                }
                PageSection("About VDOT") {
                    Card {
                        Text("VDOT is one number for your current running fitness. Your plan's paces (easy, threshold and interval) are calculated from it, so a higher VDOT means faster paces in your plan.")
                            .font(.subheadline).fixedSize(horizontal: false, vertical: true)
                        Text("It is raised after a race or hard effort, when your interval reps show you are faster than the plan assumed, or when quality sessions go easily. It is lowered after a long break or repeated missed paces. It changes in steps, not every day.")
                            .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        if !fitItems.isEmpty {
                            Text("The dashed line is a race-fit estimate from your best sustained efforts. It averages reps and jogs, so it often sits below the plan's VDOT. It is shown for comparison only.")
                                .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
            .pagePadding()
        }
        .screenStyle()
        .navigationBarTitleDisplayMode(.large)
    }
}

private struct VdotChangeRow: View {
    let point: VdotPointDTO

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(VdotSource.label(point.source)).font(.subheadline.weight(.semibold))
                Spacer()
                if let d = point.delta, abs(d) >= 0.005 {
                    Text("\(d > 0 ? "+" : "")\(Fmt.number(d, maxFraction: 2))").font(.subheadline.weight(.semibold))
                        .foregroundStyle(changeColor(d))
                }
                Text(Fmt.number(point.value ?? 0, maxFraction: 2)).font(.subheadline.monospacedDigit())
            }
            Text(DayLabel.medium(point.date)).font(.caption).foregroundStyle(.secondary)
            if let reason = point.reason, !reason.isEmpty {
                Text(reason).font(.caption).foregroundStyle(.secondary).lineLimit(4).fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

private struct EfficiencyDetailView: View {
    let points: [EfficiencyPointDTO]
    let kpis: RunKpiValues

    var body: some View {
        let c = Domain.running.color
        let items = points.sorted { $0.date < $1.date }.compactMap { p -> (Date, Double)? in
            guard let d = DayKey.date(p.date), let e = p.efficiency else { return nil }
            return (d, e)
        }
        let drift = RunStackTransforms.driftRuns(points)
        ScrollView {
            VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                Card {
                    CardHeader(title: "Aerobic Efficiency", symbol: "lungs.fill", color: c, time: items.isEmpty ? nil : "\(items.count) runs",
                               info: "How much speed you get per heartbeat on easy and long runs: metres per minute for each beat per minute. When your aerobic base improves you run faster at the same heart rate, so the number goes up.")
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        MetricText(value: kpis.efficiencyCurrent.map { Fmt.number($0, maxFraction: 2) } ?? "–", size: .largeTitle)
                        if let p = kpis.efficiencyChangePct {
                            Label("\(Fmt.percent(p)) in 4 weeks", systemImage: changeSymbol(p, threshold: 0.5))
                                .font(.footnote.weight(.semibold)).foregroundStyle(changeColor(p, threshold: 0.5))
                        }
                    }
                    if items.count < 2 {
                        ChartEmpty(symbol: "waveform.path.ecg", message: items.isEmpty ? "Needs a few easy runs with heart rate." : "One run so far. The trend starts with the next one.", height: 100)
                    } else {
                        Chart(Array(items.enumerated()), id: \.offset) { _, item in
                            LineMark(x: .value("Date", item.0), y: .value("Efficiency", item.1))
                                .interpolationMethod(.monotone).foregroundStyle(c)
                                .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round))
                            PointMark(x: .value("Date", item.0), y: .value("Efficiency", item.1))
                                .symbolSize(40).foregroundStyle(c)
                        }
                        .chartYScale(domain: .automatic(includesZero: false))
                        .chartYAxis { AxisMarks { _ in AxisGridLine().foregroundStyle(.quaternary); AxisValueLabel() } }
                        .frame(height: 200)
                        .accessibilityLabel("Aerobic efficiency over \(items.count) runs")
                        .accessibilityValue("Latest \(Fmt.number(items.last?.1 ?? 0, maxFraction: 2)), first \(Fmt.number(items.first?.1 ?? 0, maxFraction: 2))")
                    }
                }
                PageSection("Heart-Rate Drift", info: "A flag marks an easy run in the last 4 weeks where your heart rate crept up compared with your pace (more than 5 %). Heat, fatigue or starting too fast are the usual causes.") {
                    Card(padding: drift.isEmpty ? 16 : 0) {
                        if drift.isEmpty {
                            Text(flagText(0)).font(.subheadline).foregroundStyle(.secondary)
                        } else {
                            VStack(spacing: 0) {
                                ForEach(Array(drift.enumerated()), id: \.element.id) { i, p in
                                    if i > 0 { Divider().padding(.leading, 16) }
                                    HStack {
                                        Text(DayLabel.medium(p.date)).font(.body)
                                        Spacer(minLength: 8)
                                        Text(Fmt.percent(p.decouplingPct, signed: false)).metricStyle(.headline, weight: .semibold).foregroundStyle(.orange)
                                    }
                                    .padding(.vertical, 10).padding(.horizontal, 16)
                                    .accessibilityElement(children: .combine)
                                }
                            }
                        }
                    }
                } trailing: {
                    if !drift.isEmpty { Text(flagText(drift.count)).font(.subheadline).foregroundStyle(.secondary) }
                }
            }
            .pagePadding()
        }
        .screenStyle()
        .navigationBarTitleDisplayMode(.large)
    }

    private func flagText(_ listed: Int) -> String {
        let n = max(kpis.driftFlags, listed)
        return n == 0 ? "No drift flags. Heart rate stayed steady." : "\(n) \(n == 1 ? "flag" : "flags")"
    }
}

// MARK: - Time in zones

private struct ZonesCard: View {
    let weeks: [RunWeekDTO]

    var body: some View {
        let segments = RunTransforms.zoneSegments(weeks)
        Card {
            CardHeader(title: "Heart-Rate Zones", symbol: "heart.fill", color: Domain.heart.color,
                       time: weeks.isEmpty ? nil : "\(weeks.count) weeks", info: "Minutes per week in each heart-rate zone, Z1 easy to Z5 maximal.")
            if segments.isEmpty {
                ChartEmpty(symbol: "heart.text.square", message: "Zone data shows up after the first run with heart rate.", height: 110)
            } else {
                Chart(segments) { seg in
                    BarMark(x: .value("Week", DayKey.date(seg.weekStart) ?? .now, unit: .weekOfYear),
                            y: .value("Minutes", seg.minutes))
                        .foregroundStyle(by: .value("Zone", seg.name))
                }
                .chartForegroundStyleScale(domain: RunTransforms.zoneNames, range: ChartPalette.zones)
                .chartXAxis { AxisMarks(values: .stride(by: .weekOfYear, count: 2)) { _ in AxisValueLabel(format: .dateTime.month(.abbreviated).day(), anchor: .top) } }
                .chartLegend(position: .bottom, alignment: .leading, spacing: 8)
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine().foregroundStyle(.quaternary); AxisValueLabel { if let d = v.as(Double.self) { Text("\(Fmt.number(d, maxFraction: 0))") } } } }
                .frame(height: 190)
                .accessibilityLabel("Weekly minutes in heart-rate zones one to five")
                .accessibilityValue(summary(segments))
            }
        }
    }

    private func summary(_ segments: [ZoneSegment]) -> String {
        guard let last = segments.last?.weekStart else { return "" }
        let mins = segments.filter { $0.weekStart == last }.map { "\($0.name) \(Int($0.minutes.rounded())) minutes" }
        return "Latest week: " + mins.joined(separator: ", ")
    }
}

// MARK: - Recent runs

private struct RecentRunsSection: View {
    let runs: [RunSummaryDTO]
    let loaded: Bool

    var body: some View {
        PageSection("Recent Runs") {
            if runs.isEmpty {
                Card { ChartEmpty(symbol: "figure.run", message: loaded ? "No runs yet." : "Loading runs…", height: 80) }
            } else {
                RunRows(runs: Array(runs.prefix(5)))
            }
        } trailing: {
            if runs.count > 5 { NavigationLink("Show All", value: RunningRoute.allRuns) }
        }
    }
}

/// All synced runs (the latest 30), each opening its analysis.
private struct AllRunsView: View {
    @Environment(AppEnvironment.self) private var env

    private var recent: StatsResource<[RunSummaryDTO]> { env.stats.resource(StatsEndpoints.recentRuns()) }

    var body: some View {
        let recent = recent
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let runs = recent.value, !runs.isEmpty {
                    RunRows(runs: runs)
                } else {
                    StatsPlaceholder(resource: recent, symbol: "figure.run", emptyTitle: "runs")
                }
                FreshnessLabel(resource: recent)
            }
            .pagePadding()
        }
        .screenStyle()
        .navigationTitle("Runs")
        .refreshable { await refreshStats(env, [recent]) }
        .loadsStats([recent])
    }
}

private struct RunRows: View {
    let runs: [RunSummaryDTO]

    var body: some View {
        Card(padding: 0) {
            VStack(spacing: 0) {
                ForEach(Array(runs.enumerated()), id: \.element.id) { i, r in
                    if i > 0 { Divider().padding(.leading, 16) }
                    NavigationLink(value: RunRoute(id: r.id)) { RunRow(run: r) }.buttonStyle(.plain)
                }
            }
        }
    }
}

private struct RunRow: View {
    let run: RunSummaryDTO

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(run.title ?? "Run").font(.body.weight(.medium)).lineLimit(1)
                Text(DayLabel.medium(run.day)).font(.subheadline).foregroundStyle(.secondary)
            }
            Spacer(minLength: 6)
            VStack(alignment: .trailing, spacing: 1) {
                MetricText(value: run.distanceM.map { Fmt.km($0 / 1000) } ?? "–", unit: "km", size: .headline)
                Text(Fmt.paceKm(run.avgPaceSPerKm)).font(.caption).foregroundStyle(.secondary)
            }
            DisclosureChevron()
        }
        .padding(.vertical, 10).padding(.horizontal, 16)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(run.title ?? "Run") on \(DayLabel.medium(run.day)), \(Fmt.meters(run.distanceM)), pace \(Fmt.paceKm(run.avgPaceSPerKm))")
    }
}
