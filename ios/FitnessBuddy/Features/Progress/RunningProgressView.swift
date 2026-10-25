import Charts
import SwiftUI

struct RunningProgressView: View {
    @Environment(AppEnvironment.self) private var env
    @Binding var openRun: RunRoute?

    private var running: StatsResource<RunningStatsDTO> { env.stats.resource(StatsEndpoints.running()) }
    private var recent: StatsResource<[RunSummaryDTO]> { env.stats.resource(StatsEndpoints.recentRuns()) }

    var body: some View {
        let running = running, recent = recent
        ScrollView {
            VStack(spacing: 16) {
                if let data = running.value {
                    let weeks = RunTransforms.weeksWithRuns(data.weekly)
                    WeeklyKmCard(weeks: weeks)
                    ZonesCard(weeks: weeks)
                    EfficiencyCard(points: data.efficiency, open: { openRun = RunRoute(id: $0) })
                    VdotCard(points: data.vdot)
                    IntervalsCard(sessions: data.intervals)
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
    }
}

private let garminSinceNote = "Garmin data since Aug 2026"

// MARK: - Weekly km

private struct WeeklyKmCard: View {
    let weeks: [RunWeekDTO]
    @State private var selected: Date?

    private var bars: [(date: Date, week: RunWeekDTO)] { weeks.compactMap { w in DayKey.date(w.weekStart).map { ($0, w) } } }

    var body: some View {
        let bars = bars
        let sel = selected.flatMap { s in bars.min { abs($0.date.timeIntervalSince(s)) < abs($1.date.timeIntervalSince(s)) } }
        ChartCard(title: "Weekly distance", symbol: "figure.run", color: Domain.running.color) {
            if bars.isEmpty {
                ChartEmpty(symbol: "figure.run", message: "No runs synced yet. Your Garmin runs show up here.")
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text(Fmt.km(bars.last?.week.km ?? 0)).metricStyle(.largeTitle).foregroundStyle(Domain.running.color)
                    Text("km latest week").font(.subheadline).foregroundStyle(.secondary)
                }
                Chart {
                    ForEach(bars, id: \.week.weekStart) { item in
                        BarMark(x: .value("Week", item.date, unit: .weekOfYear), y: .value("km", item.week.km ?? 0))
                            .foregroundStyle(Domain.running.color.gradient)
                            .opacity(sel == nil || sel?.week.weekStart == item.week.weekStart ? 1 : 0.4)
                            .cornerRadius(4)
                    }
                    if let sel {
                        RuleMark(x: .value("Week", sel.date, unit: .weekOfYear))
                            .foregroundStyle(.secondary.opacity(0.4))
                            .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("Week of \(DayLabel.short(sel.week.weekStart))").font(.caption.weight(.semibold))
                                    Text("\(Fmt.km(sel.week.km ?? 0)) km, \(sel.week.runs ?? 0) runs").font(.caption)
                                }
                                .padding(8).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
                            }
                    }
                }
                .chartXSelection(value: $selected)
                .chartXAxis { AxisMarks(values: .stride(by: .weekOfYear, count: 3)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated).day(), anchor: .top) } }
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text("\(Fmt.number(d, maxFraction: 0))") } } } }
                .frame(height: 170)
                .accessibilityLabel("Weekly running distance in kilometres, \(bars.count) weeks")
                .accessibilityValue("Latest week \(Fmt.km(bars.last?.week.km ?? 0)) kilometres")
                ChartNote(text: garminSinceNote)
            }
        }
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
                .chartXAxis { AxisMarks(values: .stride(by: .weekOfYear, count: 3)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated).day(), anchor: .top) } }
                .chartLegend(position: .bottom, alignment: .leading, spacing: 8)
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text("\(Fmt.number(d, maxFraction: 0))") } } } }
                .frame(height: 190)
                .accessibilityLabel("Weekly minutes in heart-rate zones one to five")
                .accessibilityValue(summary(segments))
                ChartNote(text: garminSinceNote)
            }
        }
    }

    private func summary(_ segments: [ZoneSegment]) -> String {
        guard let last = segments.last?.weekStart else { return "" }
        let mins = segments.filter { $0.weekStart == last }.map { "\($0.name) \(Int($0.minutes.rounded())) minutes" }
        return "Latest week: " + mins.joined(separator: ", ")
    }
}

// MARK: - Aerobic efficiency

private struct EfficiencyCard: View {
    let points: [EfficiencyPointDTO]
    let open: (String) -> Void

    private var items: [(date: Date, point: EfficiencyPointDTO, value: Double)] {
        points.sorted { $0.date < $1.date }.compactMap { p in
            guard let d = DayKey.date(p.date), let e = p.efficiency else { return nil }
            return (d, p, e)
        }
    }

    var body: some View {
        let items = items
        ChartCard(title: "Aerobic efficiency", subtitle: "Metres per minute per heartbeat on easy and long runs. Higher is fitter.", symbol: "lungs.fill", color: Domain.running.color) {
            if items.count < 2 {
                ChartEmpty(symbol: "waveform.path.ecg", message: items.isEmpty ? "Needs a few easy runs with heart rate." : "One run so far. The trend starts with the next one.", height: 120)
            } else {
                Chart(items, id: \.point.runId) { item in
                    LineMark(x: .value("Date", item.date), y: .value("Efficiency", item.value))
                        .interpolationMethod(.monotone)
                        .foregroundStyle(Domain.running.color)
                    PointMark(x: .value("Date", item.date), y: .value("Efficiency", item.value))
                        .symbolSize(60)
                        .foregroundStyle(Domain.running.color)
                }
                .chartYScale(domain: .automatic(includesZero: false))
                .chartOverlay { proxy in
                    GeometryReader { geo in
                        Rectangle().fill(.clear).contentShape(Rectangle())
                            .onTapGesture { location in
                                guard let plot = proxy.plotFrame else { return }
                                let x = location.x - geo[plot].origin.x
                                guard let date: Date = proxy.value(atX: x) else { return }
                                if let hit = RunTransforms.nearest(items.map(\.point), to: date, key: \.date) { open(hit.runId) }
                            }
                    }
                }
                .frame(height: 170)
                .accessibilityLabel("Aerobic efficiency over \(items.count) runs")
                .accessibilityValue("Latest \(Fmt.number(items.last?.value ?? 0, maxFraction: 2)), first \(Fmt.number(items.first?.value ?? 0, maxFraction: 2))")
                ChartNote(text: "Tap a point to open that run.", symbol: "hand.tap")
                ChartNote(text: garminSinceNote)
            }
        }
    }
}

// MARK: - VDOT

private struct VdotCard: View {
    let points: [VdotPointDTO]

    var body: some View {
        let items = points.sorted { $0.date < $1.date }.compactMap { p -> (Date, Double)? in
            guard let d = DayKey.date(p.date), let v = p.value else { return nil }
            return (d, v)
        }
        ChartCard(title: "VDOT", subtitle: "Fitness estimate from your hard efforts", symbol: "speedometer", color: Domain.running.color) {
            if let last = items.last {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text(Fmt.number(last.1, maxFraction: 1)).metricStyle(.largeTitle).foregroundStyle(Domain.running.color)
                    Text("current").font(.subheadline).foregroundStyle(.secondary)
                }
            }
            if items.count < 2 {
                ChartEmpty(symbol: "speedometer", message: items.isEmpty ? "Your VDOT appears after a hard interval or tempo run." : "A trend needs a second data point.", height: 100)
            } else {
                Chart(Array(items.enumerated()), id: \.offset) { _, item in
                    LineMark(x: .value("Date", item.0), y: .value("VDOT", item.1))
                        .interpolationMethod(.stepEnd)
                        .foregroundStyle(Domain.running.color)
                    PointMark(x: .value("Date", item.0), y: .value("VDOT", item.1))
                        .foregroundStyle(Domain.running.color)
                }
                .chartYScale(domain: .automatic(includesZero: false))
                .frame(height: 140)
                .accessibilityLabel("VDOT trend, \(items.count) values")
                .accessibilityValue("Latest \(Fmt.number(items.last?.1 ?? 0, maxFraction: 1))")
            }
        }
    }
}

// MARK: - Intervals

private struct IntervalsCard: View {
    let sessions: [IntervalSessionDTO]

    var body: some View {
        let sorted = sessions.sorted { $0.date > $1.date }
        ChartCard(title: "Interval sessions", symbol: "bolt.heart.fill", color: Domain.running.color) {
            if sorted.isEmpty {
                ChartEmpty(symbol: "bolt.heart", message: "No interval sessions yet.", height: 80)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(sorted.enumerated()), id: \.element.id) { i, s in
                        NavigationLink(value: RunRoute(id: s.runId)) { IntervalRow(session: s) }.buttonStyle(.plain)
                        if i < sorted.count - 1 { Divider() }
                    }
                }
            }
        }
    }
}

struct PassBadge: View {
    let passed: Bool?
    var body: some View {
        switch passed {
        case true?: Label("Passed", systemImage: "checkmark.circle.fill").font(.caption.weight(.semibold)).foregroundStyle(.green)
        case false?: Label("Missed", systemImage: "xmark.circle.fill").font(.caption.weight(.semibold)).foregroundStyle(.orange)
        case nil: Label("Not rated", systemImage: "circle.dashed").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
        }
    }
}

private struct IntervalRow: View {
    let session: IntervalSessionDTO

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(DayLabel.medium(session.date)).font(.subheadline.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 6)
            PassBadge(passed: session.passed)
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Intervals on \(DayLabel.medium(session.date)), \(session.reps ?? 0) reps, \(session.passed == true ? "passed" : session.passed == false ? "missed" : "not rated")")
    }

    private var detail: String {
        var parts: [String] = []
        if let r = session.reps { parts.append("\(r) reps") }
        if let p = session.avgWorkPaceSPerKm {
            parts.append(Fmt.paceKm(p) + (session.targetPaceSPerKm.map { " (target \(Fmt.pace($0)))" } ?? ""))
        }
        if let hr = session.avgWorkHr { parts.append("\(Int(hr.rounded())) bpm") }
        if session.hrInRange == false { parts.append("HR out of range") }
        if session.recoveryOk == false { parts.append("short recovery") }
        return parts.joined(separator: " · ")
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
