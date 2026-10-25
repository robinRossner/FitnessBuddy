import Charts
import SwiftData
import SwiftUI

struct RunningProgressView: View {
    @Environment(AppEnvironment.self) private var env
    @Binding var openRun: RunRoute?
    @Query private var planned: [CachedPlannedSession]
    @State private var sheet: KpiSheet?

    enum KpiSheet: String, Identifiable {
        case vdot, efficiency
        var id: String { rawValue }
    }

    private var running: StatsResource<RunningStatsDTO> { env.stats.resource(StatsEndpoints.running()) }
    private var recent: StatsResource<[RunSummaryDTO]> { env.stats.resource(StatsEndpoints.recentRuns()) }

    /// Runs planned for the current week in the cached plan (fallback when the stats carry no plan figure).
    private var plannedRunsThisWeek: Int? {
        let days = Set(AppClock.weekDays(containing: AppClock.now))
        let n = planned.filter { days.contains($0.date) && Domain.forKind($0.kind) == .running }.count
        return n > 0 ? n : nil
    }

    init(openRun: Binding<RunRoute?>) {
        _openRun = openRun
        #if DEBUG
        let d = UserDefaults.standard.string(forKey: "uiSheet")
        _sheet = State(initialValue: d == "vdot" ? .vdot : (d == "efficiency" ? .efficiency : nil))
        #endif
    }

    var body: some View {
        let running = running, recent = recent
        ScrollView {
            VStack(spacing: 16) {
                if let data = running.value {
                    let kpis = RunStackTransforms.kpis(data, plannedRunsThisWeek: plannedRunsThisWeek)
                    RunKpiCard(kpis: kpis, open: { sheet = $0 })
                    WeeklyKmCard(stacks: RunStackTransforms.stacks(data.weekly, last: 12), open: { openRun = RunRoute(id: $0) })
                    ZonesCard(weeks: Array(RunTransforms.weeksWithRuns(data.weekly).suffix(12)))
                } else {
                    StatsPlaceholder(resource: running, symbol: "figure.run", emptyTitle: "running stats")
                }
                RecentRunsCard(runs: recent.value ?? [], loaded: recent.value != nil)
                FreshnessLabel(resource: running)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .refreshable { await refreshStats(env, [running, recent]) }
        .loadsStats([running, recent])
        .sheet(item: $sheet) { which in
            if let data = running.value {
                let kpis = RunStackTransforms.kpis(data, plannedRunsThisWeek: plannedRunsThisWeek)
                switch which {
                case .vdot: VdotSheet(points: data.vdot, kpis: kpis)
                case .efficiency: EfficiencySheet(points: data.efficiency, kpis: kpis)
                }
            }
        }
    }
}

private let garminSinceNote = "Garmin data since Aug 2026"

// MARK: - KPI tiles

private func changeColor(_ delta: Double?, threshold: Double = 0.05) -> Color {
    guard let delta else { return .secondary }
    if abs(delta) <= threshold { return .secondary }
    return delta > 0 ? .green : .orange
}

private func changeSymbol(_ delta: Double?, threshold: Double = 0.05) -> String {
    guard let delta, abs(delta) > threshold else { return "arrow.right" }
    return delta > 0 ? "arrow.up.right" : "arrow.down.right"
}

private struct TappableKpiTile<Caption: View>: View {
    let title: String
    let value: String
    var unit: String? = nil
    let action: () -> Void
    @ViewBuilder var caption: Caption

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline) {
                    Text(title).font(.caption.weight(.medium)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 2)
                    Image(systemName: "chevron.right").font(.caption2.weight(.semibold)).foregroundStyle(.tertiary).accessibilityHidden(true)
                }
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text(value).metricStyle(.title).foregroundStyle(Domain.running.color)
                    if let unit { Text(unit).font(.footnote.weight(.medium)).foregroundStyle(.secondary) }
                }
                caption.font(.caption.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(Color.screenBackground.opacity(0.6), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }
}

private struct RunKpiCard: View {
    let kpis: RunKpiValues
    let open: (RunningProgressView.KpiSheet) -> Void

    var body: some View {
        Card(tint: Domain.running.color) {
            SectionLabel(title: "Running", symbol: "figure.run", color: Domain.running.color)
            KpiGrid {
                TappableKpiTile(title: "VDOT", value: kpis.vdotCurrent.map { Fmt.number($0, maxFraction: 1) } ?? "—", action: { open(.vdot) }) {
                    if let d = kpis.vdotChange {
                        Label(abs(d) < 0.05 ? "unchanged vs 4 weeks ago" : "\(d > 0 ? "+" : "")\(Fmt.number(d, maxFraction: 1)) vs 4 weeks ago", systemImage: changeSymbol(d))
                            .foregroundStyle(changeColor(d))
                    } else {
                        Text(kpis.vdotCurrent == nil ? "needs a hard effort" : "no 4-week comparison").foregroundStyle(.secondary)
                    }
                }
                TappableKpiTile(title: "Aerobic efficiency", value: kpis.efficiencyChangePct.map { Fmt.percent($0) } ?? kpis.efficiencyCurrent.map { Fmt.number($0, maxFraction: 2) } ?? "—",
                                action: { open(.efficiency) }) {
                    if let p = kpis.efficiencyChangePct {
                        Label("vs 4 weeks ago", systemImage: changeSymbol(p, threshold: 0.5)).foregroundStyle(changeColor(p, threshold: 0.5))
                    } else {
                        Text(kpis.efficiencyCurrent == nil ? "needs more easy runs" : "no 4-week comparison").foregroundStyle(.secondary)
                    }
                }
                KpiTile(title: "Runs this week", value: kpis.runsPlanned.map { "\(kpis.runsThisWeek)/\($0)" } ?? "\(kpis.runsThisWeek)",
                        caption: kpis.runsPlanned == nil ? "no plan figure yet" : (kpis.runsThisWeek >= (kpis.runsPlanned ?? 0) ? "on plan" : "to go: \((kpis.runsPlanned ?? 0) - kpis.runsThisWeek)"),
                        captionColor: kpis.runsPlanned.map { kpis.runsThisWeek >= $0 } == true ? .green : .secondary,
                        color: Domain.running.color)
                KpiTile(title: "Distance this week", value: Fmt.km(kpis.kmThisWeek), unit: "km", color: Domain.running.color)
            }
        }
    }
}

// MARK: - Weekly km, stacked by run

private struct WeeklyKmCard: View {
    let stacks: [RunWeekStack]
    let open: (String) -> Void
    @State private var selected: Date?
    @State private var pickedWeek: String?

    private var shown: RunWeekStack? {
        if let pickedWeek, let hit = stacks.first(where: { $0.weekStart == pickedWeek }) { return hit }
        return stacks.last
    }

    private var typesPresent: [RunType] {
        let present = Set(stacks.flatMap(\.segments).map(\.type))
        return RunType.allCases.filter { present.contains($0) }
    }

    var body: some View {
        ChartCard(title: "Weekly distance", subtitle: "Each block is one run. The number above a bar is how many runs that week.",
                  symbol: "figure.run", color: Domain.running.color) {
            if stacks.isEmpty {
                ChartEmpty(symbol: "figure.run", message: "No runs synced yet. Your Garmin runs show up here.")
            } else {
                Chart {
                    ForEach(stacks) { week in
                        ForEach(week.segments) { seg in
                            BarMark(x: .value("Week", week.date, unit: .weekOfYear), yStart: .value("From", seg.kmStart), yEnd: .value("To", seg.kmEnd))
                                .foregroundStyle(by: .value("Type", seg.type.title))
                                .cornerRadius(2)
                                .opacity(shown?.weekStart == week.weekStart ? 1 : 0.55)
                        }
                        if week.runCount > 0 {
                            PointMark(x: .value("Week", week.date, unit: .weekOfYear), y: .value("Total", week.totalKm))
                                .opacity(0)
                                .annotation(position: .top, spacing: 2) {
                                    Text("\(week.runCount)").font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                                }
                        }
                    }
                }
                .chartForegroundStyleScale(domain: RunType.allCases.map(\.title), range: RunType.allCases.map(ChartPalette.runType))
                .chartLegend(.hidden)
                .chartXSelection(value: $selected)
                .chartXAxis { AxisMarks(values: .stride(by: .weekOfYear, count: 2)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated).day(), anchor: .top) } }
                .chartYScale(domain: 0...max(5, (stacks.map(\.totalKm).max() ?? 0) * 1.18))
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text("\(Fmt.number(d, maxFraction: 0))") } } } }
                .frame(height: 190)
                .onChange(of: selected) { _, new in
                    guard let new, let hit = stacks.min(by: { abs($0.date.timeIntervalSince(new)) < abs($1.date.timeIntervalSince(new)) }) else { return }
                    pickedWeek = hit.weekStart
                }
                .accessibilityLabel("Weekly running distance in kilometres, \(stacks.count) weeks, one block per run")
                .accessibilityValue("Latest week \(Fmt.km(stacks.last?.totalKm ?? 0)) kilometres in \(stacks.last?.runCount ?? 0) runs")

                HStack(spacing: 14) {
                    ForEach(typesPresent) { t in
                        HStack(spacing: 5) {
                            RoundedRectangle(cornerRadius: 2).fill(ChartPalette.runType(t)).frame(width: 10, height: 10)
                            Text(t.title).foregroundStyle(.secondary)
                        }
                    }
                }
                .font(.caption)
                .accessibilityHidden(true)

                if let week = shown { WeekRuns(week: week, open: open) }
            }
        }
    }
}

private struct WeekRuns: View {
    let week: RunWeekStack
    let open: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Week of \(DayLabel.short(week.weekStart)) · \(week.runCount) \(week.runCount == 1 ? "run" : "runs") · \(Fmt.km(week.totalKm)) km")
                .font(.subheadline.weight(.semibold)).padding(.vertical, 8)
                .fixedSize(horizontal: false, vertical: true)
            if week.segments.isEmpty {
                Text("No runs this week.").font(.footnote).foregroundStyle(.secondary).padding(.bottom, 4)
            }
            ForEach(Array(week.segments.enumerated()), id: \.element.id) { i, seg in
                Divider()
                if seg.isRun {
                    Button { open(seg.runId) } label: { row(seg) }.buttonStyle(.plain)
                } else {
                    row(seg)
                }
            }
        }
    }

    private func row(_ seg: RunSegment) -> some View {
        HStack(spacing: 10) {
            Circle().fill(ChartPalette.runType(seg.type)).frame(width: 10, height: 10).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(seg.isRun ? seg.type.title : "All runs").font(.subheadline.weight(.medium))
                if let d = seg.date { Text(DayLabel.medium(d)).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 8)
            Text("\(Fmt.km(seg.km)) km").metricStyle(.subheadline, weight: .semibold)
            if seg.isRun { Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary).accessibilityHidden(true) }
        }
        .padding(.vertical, 9)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(seg.type.title) run\(seg.date.map { " on \(DayLabel.medium($0))" } ?? ""), \(Fmt.km(seg.km)) kilometres")
        .accessibilityAddTraits(seg.isRun ? .isButton : [])
    }
}

// MARK: - Time in zones

private struct ZonesCard: View {
    let weeks: [RunWeekDTO]

    var body: some View {
        let segments = RunTransforms.zoneSegments(weeks)
        ChartCard(title: "Time in heart-rate zones", subtitle: "Minutes per week, Z1 easy to Z5 maximal", symbol: "heart.fill", color: ChartPalette.zone(4)) {
            if segments.isEmpty {
                ChartEmpty(symbol: "heart.text.square", message: "Zone data shows up after the first run with heart rate.", height: 110)
            } else {
                Chart(segments) { seg in
                    BarMark(x: .value("Week", DayKey.date(seg.weekStart) ?? .now, unit: .weekOfYear),
                            y: .value("Minutes", seg.minutes))
                        .foregroundStyle(by: .value("Zone", seg.name))
                }
                .chartForegroundStyleScale(domain: RunTransforms.zoneNames, range: ChartPalette.zones)
                .chartXAxis { AxisMarks(values: .stride(by: .weekOfYear, count: 2)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated).day(), anchor: .top) } }
                .chartLegend(position: .bottom, alignment: .leading, spacing: 8)
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text("\(Fmt.number(d, maxFraction: 0))") } } } }
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

// MARK: - KPI sheets

private struct VdotSheet: View {
    @Environment(\.dismiss) private var dismiss
    let points: [VdotPointDTO]
    let kpis: RunKpiValues

    var body: some View {
        let items = points.sorted { $0.date < $1.date }.compactMap { p -> (Date, Double)? in
            guard let d = DayKey.date(p.date), let v = p.value else { return nil }
            return (d, v)
        }
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    ChartCard(title: "VDOT trend", symbol: "speedometer", color: Domain.running.color) {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text(kpis.vdotCurrent.map { Fmt.number($0, maxFraction: 1) } ?? "–").metricStyle(.largeTitle).foregroundStyle(Domain.running.color)
                            if let d = kpis.vdotChange {
                                Label("\(d > 0 ? "+" : "")\(Fmt.number(d, maxFraction: 1)) in 4 weeks", systemImage: changeSymbol(d))
                                    .font(.subheadline.weight(.semibold)).foregroundStyle(changeColor(d))
                            }
                        }
                        if items.count < 2 {
                            ChartEmpty(symbol: "speedometer", message: items.isEmpty ? "Your VDOT appears after a hard interval or tempo run." : "A trend needs a second data point.", height: 100)
                        } else {
                            Chart(Array(items.enumerated()), id: \.offset) { _, item in
                                LineMark(x: .value("Date", item.0), y: .value("VDOT", item.1))
                                    .interpolationMethod(.stepEnd).foregroundStyle(Domain.running.color)
                                PointMark(x: .value("Date", item.0), y: .value("VDOT", item.1)).foregroundStyle(Domain.running.color)
                            }
                            .chartYScale(domain: .automatic(includesZero: false))
                            .frame(height: 180)
                            .accessibilityLabel("VDOT trend, \(items.count) values")
                            .accessibilityValue("Latest \(Fmt.number(items.last?.1 ?? 0, maxFraction: 1))")
                        }
                    }
                    Card {
                        SectionLabel(title: "What is VDOT?", symbol: "info.circle", color: Domain.running.color)
                        Text("VDOT is one number for your current running fitness, estimated from your hardest recent efforts such as races, tempo runs and intervals. A higher VDOT means you can hold faster paces for the same effort.")
                            .font(.subheadline).fixedSize(horizontal: false, vertical: true)
                        Text("It only moves after a hard session, so it changes in steps rather than every day.")
                            .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(16)
            }
            .background(Color.screenBackground)
            .navigationTitle("VDOT")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.large])
    }
}

private struct EfficiencySheet: View {
    @Environment(\.dismiss) private var dismiss
    let points: [EfficiencyPointDTO]
    let kpis: RunKpiValues

    var body: some View {
        let items = points.sorted { $0.date < $1.date }.compactMap { p -> (Date, Double)? in
            guard let d = DayKey.date(p.date), let e = p.efficiency else { return nil }
            return (d, e)
        }
        let drift = RunStackTransforms.driftRuns(points)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    ChartCard(title: "Aerobic efficiency", symbol: "lungs.fill", color: Domain.running.color) {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text(kpis.efficiencyCurrent.map { Fmt.number($0, maxFraction: 2) } ?? "–").metricStyle(.largeTitle).foregroundStyle(Domain.running.color)
                            if let p = kpis.efficiencyChangePct {
                                Label("\(Fmt.percent(p)) in 4 weeks", systemImage: changeSymbol(p, threshold: 0.5))
                                    .font(.subheadline.weight(.semibold)).foregroundStyle(changeColor(p, threshold: 0.5))
                            }
                        }
                        if items.count < 2 {
                            ChartEmpty(symbol: "waveform.path.ecg", message: items.isEmpty ? "Needs a few easy runs with heart rate." : "One run so far. The trend starts with the next one.", height: 100)
                        } else {
                            Chart(Array(items.enumerated()), id: \.offset) { _, item in
                                LineMark(x: .value("Date", item.0), y: .value("Efficiency", item.1))
                                    .interpolationMethod(.monotone).foregroundStyle(Domain.running.color)
                                PointMark(x: .value("Date", item.0), y: .value("Efficiency", item.1))
                                    .symbolSize(50).foregroundStyle(Domain.running.color)
                            }
                            .chartYScale(domain: .automatic(includesZero: false))
                            .frame(height: 180)
                            .accessibilityLabel("Aerobic efficiency over \(items.count) runs")
                            .accessibilityValue("Latest \(Fmt.number(items.last?.1 ?? 0, maxFraction: 2)), first \(Fmt.number(items.first?.1 ?? 0, maxFraction: 2))")
                        }
                    }
                    Card {
                        SectionLabel(title: "What is aerobic efficiency?", symbol: "info.circle", color: Domain.running.color)
                        Text("How much speed you get per heartbeat on easy and long runs: metres per minute for each beat per minute. When your aerobic base improves you run faster at the same heart rate, so the number goes up.")
                            .font(.subheadline).fixedSize(horizontal: false, vertical: true)
                    }
                    Card {
                        SectionLabel(title: "Heart-rate drift, last 4 weeks", symbol: "waveform.path.ecg", color: .orange)
                        Text(flagText(drift.count))
                            .font(.subheadline.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                        Text("A flag marks an easy run where your heart rate crept up compared with your pace (more than 5 %). Heat, fatigue or starting too fast are the usual causes.")
                            .font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        ForEach(drift) { p in
                            HStack {
                                Text(DayLabel.medium(p.date)).font(.subheadline)
                                Spacer(minLength: 8)
                                Text(Fmt.percent(p.decouplingPct, signed: false)).metricStyle(.subheadline, weight: .semibold).foregroundStyle(.orange)
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
                .padding(16)
            }
            .background(Color.screenBackground)
            .navigationTitle("Aerobic efficiency")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.large])
    }

    private func flagText(_ listed: Int) -> String {
        let n = max(kpis.driftFlags, listed)
        return n == 0 ? "No drift flags. Heart rate stayed steady." : "\(n) \(n == 1 ? "flag" : "flags")"
    }
}

// MARK: - Recent runs

private struct RecentRunsCard: View {
    let runs: [RunSummaryDTO]
    let loaded: Bool

    var body: some View {
        ChartCard(title: "Recent runs", symbol: "clock.fill", color: Domain.running.color) {
            if runs.isEmpty {
                ChartEmpty(symbol: "figure.run", message: loaded ? "No runs yet." : "Loading runs…", height: 80)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(runs.prefix(15).enumerated()), id: \.element.id) { i, r in
                        NavigationLink(value: RunRoute(id: r.id)) { RunRow(run: r) }.buttonStyle(.plain)
                        if i < min(runs.count, 15) - 1 { Divider() }
                    }
                }
            }
        }
    }
}

private struct RunRow: View {
    let run: RunSummaryDTO

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(run.title ?? "Run").font(.subheadline.weight(.medium)).lineLimit(1)
                Text(DayLabel.medium(run.day)).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 6)
            VStack(alignment: .trailing, spacing: 3) {
                Text(Fmt.meters(run.distanceM)).metricStyle(.subheadline, weight: .semibold)
                Text(Fmt.paceKm(run.avgPaceSPerKm)).font(.caption).foregroundStyle(.secondary)
            }
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(run.title ?? "Run") on \(DayLabel.medium(run.day)), \(Fmt.meters(run.distanceM)), pace \(Fmt.paceKm(run.avgPaceSPerKm))")
    }
}
