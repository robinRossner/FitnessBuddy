import Charts
import SwiftData
import SwiftUI

/// One lift: e1RM chart (gradient area, PR points, range picker), stat tiles, the next planned target, recent sessions,
/// weekly volume and the personal records.
struct LiftDetailView: View {
    @Environment(AppEnvironment.self) private var env
    @Query private var planned: [CachedPlannedSession]
    @Query(filter: #Predicate<LocalSession> { $0.endedAt != nil && $0.deletedAt == nil }) private var finished: [LocalSession]
    let lift: LiftSummaryDTO
    @State private var range: LiftRange = .sixMonths
    @State private var pickedDefaultRange = false

    private var detail: StatsResource<LiftDetailDTO> { env.stats.resource(StatsEndpoints.liftDetail(lift.variantId)) }

    private var target: NextLiftTarget? {
        ProgressModel.nextTarget(variantId: lift.variantId, items: planned.compactMap(\.item),
                                 doneIds: Set(finished.compactMap(\.plannedSessionId)), today: AppClock.todayString)
    }

    var body: some View {
        let detail = detail
        let summary = detail.value?.lift ?? lift
        ScrollView {
            VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                if let data = detail.value {
                    let points = range.filter(data.points)
                    VStack(alignment: .leading, spacing: 12) {
                        E1RMChartCard(lift: summary, points: points, prs: data.prs, range: $range)
                        LiftTiles(lift: summary)
                    }
                    if let target { NextSessionCard(target: target) }
                    RecentSessionsSection(points: data.points, prs: data.prs)
                    VolumeChartCard(points: points)
                    PRListSection(prs: data.prs)
                } else {
                    StatsPlaceholder(resource: detail, symbol: "dumbbell.fill", emptyTitle: "lift history")
                    if let target { NextSessionCard(target: target) }
                }
                VStack(alignment: .leading, spacing: 12) {
                    AskCoachButton(title: "Ask coach about this lift",
                                   context: .lift(id: lift.variantId, name: summary.name.withoutGymSuffix))
                    FreshnessLabel(resource: detail)
                }
            }
            .pagePadding()
        }
        .screenStyle()
        .navigationTitle(summary.name.withoutGymSuffix)
        .navigationBarTitleDisplayMode(.large)
        .refreshable { await refreshStats(env, [detail]) }
        .loadsStats([detail])
        .onChange(of: detail.value != nil, initial: true) { _, loaded in
            // A lift not trained for a while would open on an empty chart: start on the shortest range that has a line.
            guard loaded, !pickedDefaultRange, let data = detail.value else { return }
            pickedDefaultRange = true
            range = [LiftRange.sixMonths, .year, .all].first { $0.filter(data.points).filter { $0.bestE1rm != nil }.count >= 2 } ?? .all
        }
    }
}

private extension LiftRange {
    /// "in 6 months"
    var phrase: String {
        switch self {
        case .threeMonths: "in 3 months"
        case .sixMonths: "in 6 months"
        case .year: "in 1 year"
        case .all: "all time"
        }
    }
}

// MARK: - e1RM chart

private struct E1RMChartCard: View {
    let lift: LiftSummaryDTO
    let points: [LiftPointDTO]
    let prs: [LiftPRDTO]
    @Binding var range: LiftRange
    @State private var selected: Date?

    private let c = Domain.lifting.color

    private struct Item: Identifiable {
        let date: Date
        let e1rm: Double
        let point: LiftPointDTO
        var id: String { point.id }
    }

    private var items: [Item] {
        points.compactMap { p in
            guard let d = DayKey.date(p.date), let e = p.bestE1rm else { return nil }
            return Item(date: d, e1rm: e, point: p)
        }
        .sorted { $0.date < $1.date }
    }

    private var prDays: Set<String> { Set(prs.filter { $0.type == .e1rm }.map(\.date)) }

    private func nearest(_ items: [Item]) -> Item? {
        guard let selected else { return nil }
        return items.min { abs($0.date.timeIntervalSince(selected)) < abs($1.date.timeIntervalSince(selected)) }
    }

    private var info: String {
        let base = "Best set of each session converted to a one-rep max with the Epley formula: weight × (1 + reps / 30)."
        if let gym = lift.gymName { return base + " Machines differ between gyms, so only sessions at \(gym) are included." }
        return base + " Same movement at every gym, so all sessions are included."
    }

    var body: some View {
        let items = items
        let sel = nearest(items)
        Card {
            CardHeader(title: "Estimated 1RM", symbol: "chart.line.uptrend.xyaxis", color: c, time: lift.gymName, info: info)
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                MetricText(value: (items.last?.e1rm ?? lift.latestE1rm).map { Fmt.number($0, maxFraction: 1) } ?? "–",
                           unit: "kg", size: .largeTitle)
                if let change = ProgressModel.e1rmChangePct(points) {
                    TrendBadge(delta: change, higherIsBetter: true)
                    Text(range.phrase).font(.footnote).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                if lift.isStalled { Chip(text: "Stalled", color: .orange) }
            }
            Picker("Range", selection: $range) {
                ForEach(LiftRange.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            if items.count < 2 {
                ChartEmpty(symbol: "chart.line.uptrend.xyaxis",
                           message: items.isEmpty ? "No sessions in this range. Try a longer one." : "One session so far. The line appears after the next one.")
            } else {
                chart(items, sel: sel)
                    .frame(height: 210)
                    .padding(.top, sel == nil ? 0 : 40)
                    .animation(.default, value: sel?.id)
                    .accessibilityLabel("Estimated one rep max over time\(lift.gymName.map { " at \($0)" } ?? "")")
                    .accessibilityValue(summary(items))
                HStack(spacing: 14) {
                    HStack(spacing: 4) {
                        Image(systemName: "star.fill").foregroundStyle(ChartPalette.pr)
                        Text("Personal record").foregroundStyle(.secondary)
                    }
                    Text("Touch and hold to read a session.").foregroundStyle(.secondary)
                }
                .font(.caption)
                .accessibilityHidden(true)
            }
        }
    }

    private func chart(_ items: [Item], sel: Item?) -> some View {
        let domain = yDomain(items)
        return Chart {
            ForEach(items) { item in
                AreaMark(x: .value("Date", item.date), yStart: .value("Low", domain.lowerBound), yEnd: .value("e1RM (kg)", item.e1rm))
                    .interpolationMethod(.monotone)
                    .foregroundStyle(LinearGradient(colors: [c.opacity(0.35), c.opacity(0.02)], startPoint: .top, endPoint: .bottom))
                LineMark(x: .value("Date", item.date), y: .value("e1RM (kg)", item.e1rm))
                    .interpolationMethod(.monotone)
                    .foregroundStyle(c)
                    .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                if prDays.contains(item.point.date) {
                    PointMark(x: .value("Date", item.date), y: .value("e1RM (kg)", item.e1rm))
                        .symbolSize(60)
                        .foregroundStyle(ChartPalette.pr)
                }
            }
            if let last = items.last, sel == nil {
                PointMark(x: .value("Date", last.date), y: .value("e1RM (kg)", last.e1rm))
                    .symbolSize(70).foregroundStyle(c)
            }
            if let sel {
                RuleMark(x: .value("Date", sel.date))
                    .foregroundStyle(Color.secondary.opacity(0.4))
                    .annotation(position: .top, spacing: 4, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                        Callout(item: sel.point, e1rm: sel.e1rm)
                    }
                PointMark(x: .value("Date", sel.date), y: .value("e1RM (kg)", sel.e1rm))
                    .symbolSize(90).foregroundStyle(c)
            }
        }
        .chartXSelection(value: $selected)
        .chartYScale(domain: domain)
        .chartYAxis {
            AxisMarks(position: .leading) { v in
                AxisGridLine().foregroundStyle(.quaternary)
                AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.number(d, maxFraction: 0)) } }
            }
        }
    }

    /// Padded value range; a flat series (same weight every session) must not zoom into a 1 kg window.
    private func yDomain(_ items: [Item]) -> ClosedRange<Double> {
        let values = items.map(\.e1rm)
        guard let lo = values.min(), let hi = values.max() else { return 0...100 }
        let pad = max((hi - lo) * 0.12, hi * 0.04, 2)
        return max(0, lo - pad)...(hi + pad)
    }

    private func summary(_ items: [Item]) -> String {
        guard let first = items.first, let last = items.last else { return "" }
        return "\(items.count) sessions. From \(Fmt.number(first.e1rm, maxFraction: 1)) kilograms on \(DayLabel.medium(first.point.date)) to \(Fmt.number(last.e1rm, maxFraction: 1)) kilograms on \(DayLabel.medium(last.point.date))"
    }

    private struct Callout: View {
        let item: LiftPointDTO
        let e1rm: Double
        var body: some View {
            VStack(alignment: .leading, spacing: 2) {
                Text(DayLabel.medium(item.date)).font(.caption.weight(.semibold))
                Text("e1RM \(Fmt.number(e1rm, maxFraction: 1)) kg").font(.caption.monospacedDigit())
                if let top = item.topSet, let w = top.weightKg, let r = top.reps {
                    Text("Top set \(Fmt.number(w)) kg × \(r)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            .padding(8).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

// MARK: - Tiles

private struct LiftTiles: View {
    let lift: LiftSummaryDTO

    var body: some View {
        let c = Domain.lifting.color
        KpiGrid(spacing: 12) {
            MetricTile(label: "Best e1RM", symbol: "trophy.fill", color: c,
                       value: lift.bestE1rm.map { Fmt.number($0, maxFraction: 1) } ?? "–", unit: "kg")
            MetricTile(label: "4 Weeks", symbol: lift.isStalled ? "pause.fill" : "arrow.up.right", color: c,
                       value: lift.isStalled ? "Stalled" : Fmt.percent(lift.trend4wPct),
                       info: "Change of the estimated 1RM over the last 4 weeks. Stalled means no progress for several sessions in a row.")
            MetricTile(label: "Sessions", symbol: "calendar", color: c, value: lift.sessions.map(String.init) ?? "–",
                       unit: lift.firstDate.map { "since \(DayLabel.monthYear($0))" })
            MetricTile(label: "Last PR", symbol: "star.fill", color: c,
                       value: lift.lastPrDate.map { DayLabel.short($0) } ?? "–")
        }
    }
}

// MARK: - Next session

private struct NextSessionCard: View {
    let target: NextLiftTarget

    var body: some View {
        let c = Domain.lifting.color
        Card {
            CardHeader(title: "Next Session", symbol: "target", color: c,
                       time: "\(DayLabel.weekday(target.date)), \(DayLabel.short(target.date))")
            MetricText(value: target.text, size: .title2)
            if let note = target.note {
                Text(note).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Recent sessions

private struct RecentSessionsSection: View {
    let points: [LiftPointDTO]
    let prs: [LiftPRDTO]

    var body: some View {
        let recent = Array(points.sorted { $0.date > $1.date }.prefix(8))
        let prDays = Set(prs.map(\.date))
        if !recent.isEmpty {
            PageSection("Recent Sessions") {
                Card(padding: 0) {
                    VStack(spacing: 0) {
                        ForEach(Array(recent.enumerated()), id: \.element.id) { i, p in
                            if i > 0 { Divider().padding(.leading, 16) }
                            row(p, pr: prDays.contains(p.date))
                        }
                    }
                }
            }
        }
    }

    private func row(_ p: LiftPointDTO, pr: Bool) -> some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(DayLabel.medium(p.date)).font(.body.weight(.medium))
                Text(detail(p)).font(.subheadline).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            if pr { Chip(text: "PR", color: Domain.lifting.color, symbol: "star.fill") }
            if let top = p.topSet, let w = top.weightKg, let r = top.reps {
                MetricText(value: "\(Fmt.number(w)) × \(r)", unit: "kg", size: .headline)
            }
        }
        .padding(.vertical, 10).padding(.horizontal, 16)
        .accessibilityElement(children: .combine)
    }

    private func detail(_ p: LiftPointDTO) -> String {
        var parts: [String] = []
        if let s = p.sets { parts.append("\(s) \(s == 1 ? "set" : "sets")") }
        if let v = p.volumeKg { parts.append(Fmt.volume(v)) }
        if let e = p.bestE1rm { parts.append("e1RM \(Fmt.number(e, maxFraction: 1)) kg") }
        return parts.isEmpty ? "–" : parts.joined(separator: " · ")
    }
}

// MARK: - Volume bars

private struct VolumeChartCard: View {
    let points: [LiftPointDTO]

    var body: some View {
        let c = Domain.lifting.color
        let bars = points.compactMap { p -> (Date, Double)? in
            guard let d = DayKey.date(p.date), let v = p.volumeKg else { return nil }
            return (d, v)
        }
        Card {
            CardHeader(title: "Weekly Volume", symbol: "chart.bar.fill", color: c,
                       info: "Sets × reps × weight of this lift, summed per week.")
            if bars.isEmpty {
                ChartEmpty(symbol: "chart.bar", message: "No volume in this range.", height: 100)
            } else {
                Chart(Array(bars.enumerated()), id: \.offset) { _, b in
                    BarMark(x: .value("Week", b.0, unit: .weekOfYear), y: .value("Volume (kg)", b.1))
                        .foregroundStyle(c.gradient)
                        .cornerRadius(3)
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { v in
                        AxisGridLine().foregroundStyle(.quaternary)
                        AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.number(d / 1000, maxFraction: 1) + " t") } }
                    }
                }
                .frame(height: 140)
                .accessibilityLabel("Weekly volume for this lift, \(bars.count) sessions")
                .accessibilityValue("Latest \(Fmt.volume(bars.last?.1 ?? 0))")
            }
        }
    }
}

// MARK: - PRs

private struct PRListSection: View {
    let prs: [LiftPRDTO]

    var body: some View {
        let sorted = Array(prs.sorted { $0.date > $1.date }.prefix(12))
        PageSection("Personal Records") {
            if sorted.isEmpty {
                Card { ChartEmpty(symbol: "trophy", message: "No records yet. Your first one lands soon.", height: 80) }
            } else {
                Card(padding: 0) {
                    VStack(spacing: 0) {
                        ForEach(Array(sorted.enumerated()), id: \.element.id) { i, pr in
                            if i > 0 { Divider().padding(.leading, 16) }
                            HStack {
                                Image(systemName: "trophy.fill").foregroundStyle(ChartPalette.pr).accessibilityHidden(true)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(pr.type.title).font(.body.weight(.medium))
                                    Text(DayLabel.medium(pr.date)).font(.subheadline).foregroundStyle(.secondary)
                                }
                                Spacer()
                                VStack(alignment: .trailing, spacing: 1) {
                                    valueText(pr)
                                    if pr.type != .weight, let w = pr.weightKg, let r = pr.reps {
                                        Text("\(Fmt.number(w)) kg × \(r)").font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                            }
                            .padding(.vertical, 10).padding(.horizontal, 16)
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder private func valueText(_ pr: LiftPRDTO) -> some View {
        if let v = pr.value {
            switch pr.type {
            case .reps: MetricText(value: "\(Int(v))", unit: "reps", size: .headline)
            default: MetricText(value: Fmt.number(v, maxFraction: 1), unit: "kg", size: .headline)
            }
        } else {
            Text("–").foregroundStyle(.secondary)
        }
    }
}
