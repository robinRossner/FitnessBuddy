import Charts
import SwiftUI

struct RunAnalysisView: View {
    @Environment(AppEnvironment.self) private var env
    let runId: String

    private var analysis: StatsResource<RunAnalysisDTO> { env.stats.resource(StatsEndpoints.runAnalysis(runId)) }

    var body: some View {
        let analysis = analysis
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let data = analysis.value {
                    RunHeaderCard(run: data.run, planned: data.planned)
                    RunTiles(run: data.run)
                    VerdictCard(verdict: data.verdict)
                    if !data.laps.isEmpty {
                        LapTableCard(laps: data.laps)
                        LapChartCard(laps: data.laps)
                    } else {
                        Card {
                            CardHeader(title: "Laps", symbol: "flag.checkered", color: Domain.running.color)
                            ChartEmpty(symbol: "flag.checkered", message: "This activity has no lap data.", height: 80)
                        }
                    }
                } else {
                    StatsPlaceholder(resource: analysis, symbol: "figure.run", emptyTitle: "run analysis")
                }
                AskCoachButton(title: "Ask coach about this run",
                               context: .run(id: runId, name: analysis.value?.run.name ?? "This run"))
                FreshnessLabel(resource: analysis)
            }
            .pagePadding()
        }
        .screenStyle()
        .navigationTitle("Run Analysis")
        .navigationBarTitleDisplayMode(.large)
        .refreshable { await refreshStats(env, [analysis]) }
        .loadsStats([analysis])
    }
}

private struct RunHeaderCard: View {
    let run: RunInfoDTO
    let planned: PlanItemDTO?

    var body: some View {
        let c = Domain.running.color
        Card {
            CardHeader(title: run.name ?? "Run", symbol: "figure.run", color: c, time: DayLabel.medium(run.date))
            HStack(alignment: .firstTextBaseline) {
                MetricText(value: run.distanceM.map { Fmt.km($0 / 1000) } ?? "–", unit: "km", size: .largeTitle)
                Spacer(minLength: 8)
                if let type = run.type { Chip(text: type.replacingOccurrences(of: "_", with: " ").capitalized, color: c) }
            }
            if let planned {
                Label("Planned: \(planned.title ?? planned.kind.replacingOccurrences(of: "_", with: " ").capitalized)", systemImage: "calendar")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct RunTiles: View {
    let run: RunInfoDTO

    var body: some View {
        let c = Domain.running.color
        KpiGrid(spacing: 12) {
            MetricTile(label: "Time", symbol: "stopwatch", color: c, value: run.durationS.map(Fmt.duration) ?? "–")
            MetricTile(label: "Avg Pace", symbol: "speedometer", color: c, value: run.avgPaceSPerKm.map(Fmt.pace) ?? "–",
                       unit: run.avgPaceSPerKm == nil ? nil : "/km")
            MetricTile(label: "Avg HR", symbol: "heart.fill", color: Domain.heart.color,
                       value: run.avgHr.map { "\(Int($0.rounded()))" } ?? "–", unit: run.avgHr == nil ? nil : "bpm")
            MetricTile(label: "Max HR", symbol: "heart.fill", color: Domain.heart.color,
                       value: run.maxHr.map { "\(Int($0.rounded()))" } ?? "–", unit: run.maxHr == nil ? nil : "bpm")
            MetricTile(label: "Training Effect", symbol: "flame.fill", color: c,
                       value: run.trainingEffectAerobic.map { Fmt.number($0, maxFraction: 1) } ?? "–",
                       unit: run.trainingEffectAnaerobic.map { "/ \(Fmt.number($0, maxFraction: 1))" },
                       info: "Garmin's training effect from 0 to 5: aerobic first, anaerobic after the slash.")
        }
    }
}

private struct VerdictCard: View {
    let verdict: VerdictDTO?

    var body: some View {
        let passed = verdict?.passed
        let color: Color = passed == true ? .green : (passed == false ? .orange : .secondary)
        Card {
            CardHeader(title: "Verdict", symbol: passed == true ? "checkmark.seal.fill" : (passed == false ? "exclamationmark.triangle.fill" : "questionmark.circle"),
                       color: color)
            Text(passed == true ? "Passed" : (passed == false ? "Missed the target" : "Not rated")).font(.title3.weight(.semibold))
            if let summary = verdict?.summary, !summary.isEmpty { Text(summary).font(.subheadline) }
            if let reasons = verdict?.reasons, !reasons.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(reasons, id: \.self) { reason in
                        Label { Text(reason).font(.footnote) } icon: { Image(systemName: "circle.fill").font(.system(size: 5)).foregroundStyle(color) }
                    }
                }
            }
            if verdict == nil { Text("This run has no plan to compare against.").font(.footnote).foregroundStyle(.secondary) }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Laps

private struct LapTableCard: View {
    let laps: [LapDTO]

    var body: some View {
        Card {
            CardHeader(title: "Laps", symbol: "flag.checkered", color: Domain.running.color, time: "\(laps.count)",
                       info: "Work laps are highlighted and compared with the targets.")
            VStack(spacing: 0) {
                HStack {
                    Text("#").frame(width: 24, alignment: .leading)
                    Text("Lap").frame(maxWidth: .infinity, alignment: .leading)
                    Text("Pace").frame(width: 92, alignment: .trailing)
                    Text("HR").frame(width: 76, alignment: .trailing)
                }
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary).padding(.bottom, 6)
                ForEach(laps) { lap in LapRow(lap: lap) }
            }
        }
    }
}

private struct LapRow: View {
    let lap: LapDTO

    var body: some View {
        let isWork = lap.kind == .work
        HStack(alignment: .top) {
            Text("\(lap.index)").font(.footnote.monospacedDigit()).foregroundStyle(.secondary).frame(width: 24, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(lap.kind.title).font(.subheadline.weight(isWork ? .semibold : .regular))
                Text("\(Fmt.meters(lap.distanceM)) · \(lap.durationS.map(Fmt.duration) ?? "–")").font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            cell(main: lap.avgPaceSPerKm.map(Fmt.pace) ?? "–", target: lap.targetPaceSPerKm.map { "target \(Fmt.pace($0))" }, ok: lap.paceOk)
                .frame(width: 92, alignment: .trailing)
            cell(main: lap.avgHr.map { "\(Int($0.rounded()))" } ?? "–", target: hrTarget, ok: lap.hrOk)
                .frame(width: 76, alignment: .trailing)
        }
        .padding(.vertical, 8).padding(.horizontal, isWork ? 8 : 0)
        .background(isWork ? Domain.running.color.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 10))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
    }

    private var hrTarget: String? {
        guard let lo = lap.targetHrLow, let hi = lap.targetHrHigh else { return nil }
        return "\(Int(lo.rounded()))–\(Int(hi.rounded()))"
    }

    private func cell(main: String, target: String?, ok: Bool?) -> some View {
        VStack(alignment: .trailing, spacing: 2) {
            HStack(spacing: 4) {
                if let ok {
                    Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .font(.caption).foregroundStyle(ok ? .green : .orange)
                }
                Text(main).font(.subheadline.monospacedDigit())
            }
            if let target { Text(target).font(.caption2).foregroundStyle(.secondary) }
        }
    }

    private var label: String {
        var s = "Lap \(lap.index), \(lap.kind.title), \(Fmt.meters(lap.distanceM)), pace \(Fmt.paceKm(lap.avgPaceSPerKm))"
        if let ok = lap.paceOk { s += ok ? ", pace on target" : ", pace off target" }
        if let hr = lap.avgHr { s += ", heart rate \(Int(hr.rounded()))" }
        if let ok = lap.hrOk { s += ok ? ", heart rate in range" : ", heart rate out of range" }
        return s
    }
}

private struct LapChartCard: View {
    let laps: [LapDTO]

    var body: some View {
        let pace = laps.filter { ($0.avgPaceSPerKm ?? 0) > 0 }
        let hr = laps.filter { ($0.avgHr ?? 0) > 0 }
        Card {
            CardHeader(title: "Pace and Heart Rate per Lap", symbol: "chart.xyaxis.line", color: Domain.running.color)
            if pace.isEmpty && hr.isEmpty {
                ChartEmpty(symbol: "chart.xyaxis.line", message: "No pace or heart rate on the laps.", height: 80)
            } else {
                if !pace.isEmpty {
                    Text("Pace (min/km, faster is higher)").font(.caption).foregroundStyle(.secondary)
                    Chart {
                        ForEach(pace) { lap in
                            LineMark(x: .value("Lap", lap.index), y: .value("Pace", lap.avgPaceSPerKm ?? 0), series: .value("Series", "Pace"))
                                .foregroundStyle(.secondary.opacity(0.5))
                            PointMark(x: .value("Lap", lap.index), y: .value("Pace", lap.avgPaceSPerKm ?? 0))
                                .foregroundStyle(ChartPalette.lapKind(lap.kind)).symbolSize(70)
                            if let t = lap.targetPaceSPerKm {
                                PointMark(x: .value("Lap", lap.index), y: .value("Pace", t))
                                    .symbol { Rectangle().strokeBorder(Color.secondary, lineWidth: 1.5).frame(width: 12, height: 5) }
                            }
                        }
                    }
                    .chartYScale(domain: .automatic(includesZero: false, reversed: true))
                    .chartXAxis { AxisMarks(values: .automatic(desiredCount: 6)) }
                    .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.pace(d)) } } } }
                    .frame(height: 130)
                    .accessibilityLabel("Pace per lap")
                    .accessibilityValue(pace.map { "Lap \($0.index) \(Fmt.paceKm($0.avgPaceSPerKm))" }.joined(separator: ", "))
                }
                if !hr.isEmpty {
                    Text("Heart rate (bpm), shaded band is the target").font(.caption).foregroundStyle(.secondary).padding(.top, 4)
                    Chart {
                        ForEach(hr) { lap in
                            if let lo = lap.targetHrLow, let hi = lap.targetHrHigh {
                                RectangleMark(x: .value("Lap", lap.index), yStart: .value("Low", lo), yEnd: .value("High", hi), width: 18)
                                    .foregroundStyle(Domain.running.color.opacity(0.18))
                            }
                            LineMark(x: .value("Lap", lap.index), y: .value("HR", lap.avgHr ?? 0), series: .value("Series", "HR"))
                                .foregroundStyle(.red.opacity(0.5))
                            PointMark(x: .value("Lap", lap.index), y: .value("HR", lap.avgHr ?? 0))
                                .foregroundStyle(.red).symbolSize(50)
                        }
                    }
                    .chartYScale(domain: .automatic(includesZero: false))
                    .chartXAxis { AxisMarks(values: .automatic(desiredCount: 6)) }
                    .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.number(d, maxFraction: 0)) } } } }
                    .frame(height: 130)
                    .accessibilityLabel("Heart rate per lap")
                    .accessibilityValue(hr.map { "Lap \($0.index) \(Int(($0.avgHr ?? 0).rounded())) bpm" }.joined(separator: ", "))
                }
                HStack(spacing: 12) {
                    legend("Work", ChartPalette.lapKind(.work))
                    legend("Warm-up / cool-down", ChartPalette.lapKind(.warmup))
                    legend("Recovery", ChartPalette.lapKind(.recovery))
                }
                .font(.caption2)
            }
        }
    }

    private func legend(_ text: String, _ color: Color) -> some View {
        HStack(spacing: 4) { Circle().fill(color).frame(width: 7, height: 7); Text(text).foregroundStyle(.secondary) }
    }
}
