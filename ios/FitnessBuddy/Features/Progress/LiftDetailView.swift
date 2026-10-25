import Charts
import SwiftUI

struct LiftDetailView: View {
    @Environment(AppEnvironment.self) private var env
    let lift: LiftSummaryDTO
    @State private var range: LiftRange = .sixMonths
    @State private var pickedDefaultRange = false

    private var detail: StatsResource<LiftDetailDTO> { env.stats.resource(StatsEndpoints.liftDetail(lift.variantId)) }

    var body: some View {
        let detail = detail
        let summary = detail.value?.lift ?? lift
        ScrollView {
            VStack(spacing: 16) {
                header(summary)
                if let data = detail.value {
                    let points = range.filter(data.points)
                    E1RMChartCard(points: points, prs: data.prs, range: $range, gymName: summary.gymName)
                    VolumeChartCard(points: points)
                    PRListCard(prs: data.prs)
                } else {
                    StatsPlaceholder(resource: detail, symbol: "dumbbell.fill", emptyTitle: "lift history")
                }
                AskCoachButton(title: "Ask coach about this lift",
                               context: .lift(id: lift.variantId, name: (detail.value?.lift.name ?? lift.name).withoutGymSuffix))
                FreshnessLabel(resource: detail)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Color.screenBackground)
        .navigationTitle((detail.value?.lift.name ?? lift.name).withoutGymSuffix)
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await refreshStats(env, [detail]) }
        .loadsStats([detail])
        .onChange(of: detail.value != nil, initial: true) { _, loaded in
            // A lift not trained for a while would open on an empty chart: start on the shortest range that has a line.
            guard loaded, !pickedDefaultRange, let data = detail.value else { return }
            pickedDefaultRange = true
            range = [LiftRange.sixMonths, .year, .all].first { $0.filter(data.points).filter { $0.bestE1rm != nil }.count >= 2 } ?? .all
        }
    }

    private func header(_ s: LiftSummaryDTO) -> some View {
        Card(tint: Domain.lifting.color) {
            HStack(alignment: .firstTextBaseline) {
                SectionLabel(title: MuscleGroup.display(s.muscleGroup), symbol: "dumbbell.fill", color: Domain.lifting.color)
                Spacer()
                if s.isStalled { Chip(text: "Stalled", color: .orange) }
            }
            HStack(alignment: .top, spacing: 12) {
                StatTile(title: "Latest e1RM", value: s.latestE1rm.map { Fmt.number($0, maxFraction: 1) } ?? "–", unit: "kg", color: Domain.lifting.color)
                StatTile(title: "Best e1RM", value: s.bestE1rm.map { Fmt.number($0, maxFraction: 1) } ?? "–", unit: "kg")
                StatTile(title: "4 weeks", value: Fmt.percent(s.trend4wPct), color: trendColor(s.trend4wPct))
            }
            Label {
                if let gym = s.gymName {
                    Text("\(gym). Machines differ between gyms, so only sessions at this gym are included.")
                } else {
                    Text("Same movement at every gym, so all sessions are included.")
                }
            } icon: { Image(systemName: "mappin.and.ellipse") }
            .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private func trendColor(_ pct: Double?) -> Color {
        switch Trend(pct: pct) {
        case .up: .green
        case .down: .orange
        default: .primary
        }
    }
}

// MARK: - e1RM line chart

private struct E1RMChartCard: View {
    let points: [LiftPointDTO]
    let prs: [LiftPRDTO]
    @Binding var range: LiftRange
    let gymName: String?
    @State private var selected: Date?

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
    }

    private var prDays: Set<String> { Set(prs.filter { $0.type == .e1rm }.map(\.date)) }

    private func nearest(_ items: [Item]) -> Item? {
        guard let selected else { return nil }
        return items.min { abs($0.date.timeIntervalSince(selected)) < abs($1.date.timeIntervalSince(selected)) }
    }

    var body: some View {
        let items = items
        let sel = nearest(items)
        ChartCard(title: "Estimated 1RM", symbol: "chart.line.uptrend.xyaxis", color: Domain.lifting.color) {
            Picker("Range", selection: $range) {
                ForEach(LiftRange.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            if items.count < 2 {
                ChartEmpty(symbol: "chart.line.uptrend.xyaxis",
                           message: items.isEmpty ? "No sessions in this range. Try a longer one." : "One session so far. The line appears after the next one.")
            } else {
                Chart {
                    ForEach(items) { item in
                        LineMark(x: .value("Date", item.date), y: .value("e1RM (kg)", item.e1rm))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(Domain.lifting.color)
                            .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                        if items.count <= 60 || prDays.contains(item.point.date) {
                            PointMark(x: .value("Date", item.date), y: .value("e1RM (kg)", item.e1rm))
                                .symbolSize(prDays.contains(item.point.date) ? 70 : 24)
                                .foregroundStyle(prDays.contains(item.point.date) ? Color.yellow : Domain.lifting.color)
                        }
                    }
                    if let sel {
                        RuleMark(x: .value("Date", sel.date))
                            .foregroundStyle(.secondary.opacity(0.5))
                            .annotation(position: .top, spacing: 4, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                                Callout(item: sel.point, e1rm: sel.e1rm)
                            }
                        PointMark(x: .value("Date", sel.date), y: .value("e1RM (kg)", sel.e1rm))
                            .symbolSize(90).foregroundStyle(Domain.lifting.color)
                    }
                }
                .chartXSelection(value: $selected)
                .chartYScale(domain: yDomain(items))
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.number(d, maxFraction: 0)) } } } }
                .frame(height: 220)
                .padding(.top, sel == nil ? 0 : 36)
                .animation(.default, value: sel?.id)
                .accessibilityLabel("Estimated one rep max over time\(gymName.map { " at \($0)" } ?? "")")
                .accessibilityValue(summary(items))
                HStack(spacing: 14) {
                    Label("Personal record", systemImage: "circle.fill").foregroundStyle(.yellow)
                    Text("Touch and hold to read a session.")
                }
                .font(.caption).foregroundStyle(.secondary).labelStyle(.titleAndIcon)
            }
        }
    }

    /// Padded value range; a flat series (same weight every session) must not zoom into a 1 kg window.
    private func yDomain(_ items: [Item]) -> ClosedRange<Double> {
        let values = items.map(\.e1rm)
        guard let lo = values.min(), let hi = values.max() else { return 0...100 }
        let pad = max((hi - lo) * 0.12, hi * 0.04, 2)
        return (lo - pad)...(hi + pad)
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

// MARK: - Volume bars

private struct VolumeChartCard: View {
    let points: [LiftPointDTO]

    var body: some View {
        let bars = points.compactMap { p -> (Date, Double)? in
            guard let d = DayKey.date(p.date), let v = p.volumeKg else { return nil }
            return (d, v)
        }
        ChartCard(title: "Weekly volume", subtitle: "Sets × reps × weight, summed per week", symbol: "chart.bar.fill", color: Domain.lifting.color) {
            if bars.isEmpty {
                ChartEmpty(symbol: "chart.bar", message: "No volume in this range.", height: 100)
            } else {
                Chart(Array(bars.enumerated()), id: \.offset) { _, b in
                    BarMark(x: .value("Week", b.0, unit: .weekOfYear), y: .value("Volume (kg)", b.1))
                        .foregroundStyle(Domain.lifting.color.opacity(0.75))
                        .cornerRadius(2)
                }
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.number(d / 1000, maxFraction: 1) + " t") } } } }
                .frame(height: 140)
                .accessibilityLabel("Weekly volume for this lift, \(bars.count) sessions")
                .accessibilityValue("Latest \(Fmt.volume(bars.last?.1 ?? 0))")
            }
        }
    }
}

// MARK: - PRs

private struct PRListCard: View {
    let prs: [LiftPRDTO]

    var body: some View {
        let sorted = prs.sorted { $0.date > $1.date }
        ChartCard(title: "Personal records", symbol: "trophy.fill", color: .yellow) {
            if sorted.isEmpty {
                ChartEmpty(symbol: "trophy", message: "No records yet. Your first one lands soon.", height: 80)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(sorted.prefix(12).enumerated()), id: \.element.id) { i, pr in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(pr.type.title).font(.subheadline.weight(.medium))
                                Text(DayLabel.medium(pr.date)).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 2) {
                                Text(valueText(pr)).metricStyle(.headline)
                                if pr.type != .weight, let w = pr.weightKg, let r = pr.reps {
                                    Text("\(Fmt.number(w)) kg × \(r)").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        .padding(.vertical, 8)
                        .accessibilityElement(children: .combine)
                        if i < min(sorted.count, 12) - 1 { Divider() }
                    }
                }
            }
        }
    }

    private func valueText(_ pr: LiftPRDTO) -> String {
        guard let v = pr.value else { return "–" }
        switch pr.type {
        case .reps: return "\(Int(v)) reps"
        default: return "\(Fmt.number(v, maxFraction: 1)) kg"
        }
    }
}
