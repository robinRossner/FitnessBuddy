import Charts
import SwiftUI

private func dateAxis() -> some AxisContent {
    AxisMarks(values: .automatic(desiredCount: 4)) { _ in
        AxisGridLine()
        AxisValueLabel(format: .dateTime.month(.abbreviated).day())
    }
}

private func nearest(_ days: [HealthDayDTO], to date: Date?) -> HealthDayDTO? {
    guard let date else { return nil }
    return days.min { abs((DayKey.date($0.date) ?? .distantPast).timeIntervalSince(date)) < abs((DayKey.date($1.date) ?? .distantPast).timeIntervalSince(date)) }
}

private struct DayCallout: View {
    let day: String
    let lines: [String]
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(DayLabel.medium(day)).font(.caption.weight(.semibold))
            ForEach(lines, id: \.self) { Text($0).font(.caption.monospacedDigit()) }
        }
        .padding(8).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}

private let notEnoughDays = "Needs a few days of watch data."

// MARK: - HRV

struct HRVCard: View {
    let days: [HealthDayDTO]
    @State private var selected: Date?

    var body: some View {
        let pts = days.compactMap { d -> (Date, HealthDayDTO, Double)? in
            guard let date = DayKey.date(d.date), let v = d.hrvMs else { return nil }
            return (date, d, v)
        }
        let sel = nearest(pts.map(\.1), to: selected)
        ChartCard(title: "Heart-rate variability", subtitle: "Overnight HRV. The band is within 10 % of your baseline.", symbol: "waveform.path.ecg", color: ChartPalette.hrv) {
            if pts.count < 2 {
                ChartEmpty(symbol: "waveform.path.ecg", message: notEnoughDays, height: 120)
            } else {
                Chart {
                    ForEach(pts, id: \.1.date) { date, d, _ in
                        if let b = d.hrvBaselineMs {
                            AreaMark(x: .value("Date", date), yStart: .value("Low", b * 0.9), yEnd: .value("High", b * 1.1), series: .value("Series", "band"))
                                .foregroundStyle(ChartPalette.hrv.opacity(0.14))
                                .interpolationMethod(.monotone)
                        }
                    }
                    ForEach(pts, id: \.1.date) { date, _, v in
                        LineMark(x: .value("Date", date), y: .value("HRV", v), series: .value("Series", "hrv"))
                            .foregroundStyle(ChartPalette.hrv)
                            .interpolationMethod(.monotone)
                            .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round))
                    }
                    if let sel, let date = DayKey.date(sel.date) {
                        RuleMark(x: .value("Date", date)).foregroundStyle(.secondary.opacity(0.5))
                            .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                                DayCallout(day: sel.date, lines: ["HRV \(sel.hrvMs.map { Fmt.number($0, maxFraction: 0) } ?? "–") ms",
                                                                   "Baseline \(sel.hrvBaselineMs.map { Fmt.number($0, maxFraction: 0) } ?? "–") ms"])
                            }
                    }
                }
                .chartXSelection(value: $selected)
                .chartXAxis { dateAxis() }
                .chartYScale(domain: .automatic(includesZero: false))
                .frame(height: 170)
                .accessibilityLabel("Heart rate variability over \(pts.count) days")
                .accessibilityValue("Latest \(Fmt.number(pts.last?.2 ?? 0, maxFraction: 0)) milliseconds")
            }
        }
    }
}

// MARK: - Resting HR

struct RHRCard: View {
    let days: [HealthDayDTO]

    var body: some View {
        let pts = days.compactMap { d -> (Date, HealthDayDTO, Double)? in
            guard let date = DayKey.date(d.date), let v = d.rhr else { return nil }
            return (date, d, v)
        }
        ChartCard(title: "Resting heart rate", subtitle: "Dashed line is your baseline. Lower is better.", symbol: "heart.fill", color: ChartPalette.rhr) {
            if pts.count < 2 {
                ChartEmpty(symbol: "heart", message: notEnoughDays, height: 120)
            } else {
                Chart {
                    ForEach(pts, id: \.1.date) { date, d, v in
                        LineMark(x: .value("Date", date), y: .value("bpm", v), series: .value("Series", "rhr"))
                            .foregroundStyle(ChartPalette.rhr)
                            .interpolationMethod(.monotone)
                            .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round))
                        if let b = d.rhrBaseline {
                            LineMark(x: .value("Date", date), y: .value("bpm", b), series: .value("Series", "baseline"))
                                .foregroundStyle(.secondary)
                                .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                        }
                    }
                }
                .chartXAxis { dateAxis() }
                .chartYScale(domain: .automatic(includesZero: false))
                .frame(height: 150)
                .accessibilityLabel("Resting heart rate over \(pts.count) days")
                .accessibilityValue("Latest \(Fmt.number(pts.last?.2 ?? 0, maxFraction: 0)) beats per minute")
            }
        }
    }
}

// MARK: - Sleep

struct SleepCard: View {
    let days: [HealthDayDTO]
    @State private var selected: Date?

    private static let goalHours = 8.0

    var body: some View {
        let pts = days.compactMap { d -> (Date, HealthDayDTO, Double)? in
            guard let date = DayKey.date(d.date), let h = HealthTransforms.sleepHours(d.sleepS) else { return nil }
            return (date, d, h)
        }
        let sel = nearest(pts.map(\.1), to: selected)
        let avgScore = pts.compactMap { $0.1.sleepScore }
        ChartCard(title: "Sleep", subtitle: "Hours per night. Bar colour shows the sleep score.", symbol: "bed.double.fill", color: ChartPalette.sleep) {
            if pts.isEmpty {
                ChartEmpty(symbol: "bed.double", message: notEnoughDays, height: 120)
            } else {
                Chart {
                    ForEach(pts, id: \.1.date) { date, d, h in
                        BarMark(x: .value("Date", date, unit: .day), y: .value("Hours", h))
                            .foregroundStyle(scoreColor(d.sleepScore))
                            .cornerRadius(3)
                    }
                    RuleMark(y: .value("Goal", Self.goalHours))
                        .foregroundStyle(.secondary.opacity(0.6))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                        .annotation(position: .top, alignment: .leading, spacing: 2) { Text("8 h").font(.caption2).foregroundStyle(.secondary) }
                    if let sel, let date = DayKey.date(sel.date) {
                        RuleMark(x: .value("Date", date)).foregroundStyle(.secondary.opacity(0.4))
                            .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                                DayCallout(day: sel.date, lines: [Fmt.sleep(sel.sleepS), "Score \(sel.sleepScore.map { Fmt.number($0, maxFraction: 0) } ?? "–")"])
                            }
                    }
                }
                .chartXSelection(value: $selected)
                .chartXAxis { dateAxis() }
                .chartYAxis { AxisMarks(position: .leading, values: [0, 4, 8]) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text("\(Int(d)) h") } } } }
                .frame(height: 170)
                .accessibilityLabel("Sleep hours over \(pts.count) nights")
                .accessibilityValue("Average \(Fmt.number(pts.map(\.2).reduce(0, +) / Double(pts.count), maxFraction: 1)) hours" + (avgScore.isEmpty ? "" : ", average score \(Int((avgScore.reduce(0, +) / Double(avgScore.count)).rounded()))"))
                HStack(spacing: 12) {
                    key("Score 80+", scoreColor(85)); key("60–79", scoreColor(70)); key("Below 60", scoreColor(40)); key("No score", scoreColor(nil))
                }
                .font(.caption2)
            }
        }
    }

    private func scoreColor(_ score: Double?) -> Color {
        guard let score else { return ChartPalette.sleep.opacity(0.3) }
        if score >= 80 { return ChartPalette.sleep }
        if score >= 60 { return ChartPalette.sleep.opacity(0.6) }
        return .orange.opacity(0.85)
    }

    private func key(_ text: String, _ color: Color) -> some View {
        HStack(spacing: 4) { RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 8, height: 8); Text(text).foregroundStyle(.secondary) }
    }
}

// MARK: - Steps

struct StepsCard: View {
    /// Series of the whole loaded history (so the first days of the window already have a full 7-day average).
    let series: [StepPoint]
    let range: HealthRange
    let summary: HealthSummaryDTO?
    let days: [HealthDayDTO]
    @State private var selected: Date?

    static let goal = 10_000.0

    var body: some View {
        let key = DayKey.string(DayKey.daysAgo(range.rawValue - 1, from: .now))
        let pts = series.filter { $0.date >= key }.compactMap { p -> (Date, StepPoint)? in
            DayKey.date(p.date).map { ($0, p) }
        }
        let atGoal = HealthTransforms.daysAtGoal(days, summary: summary)
        let avg = HealthTransforms.latestStepsAvg(series, summary: summary)
        let sel = selected.flatMap { s in pts.min { abs($0.0.timeIntervalSince(s)) < abs($1.0.timeIntervalSince(s)) } }
        ChartCard(title: "Steps", subtitle: "Bars are single days, the line is the 7-day average.", symbol: "figure.walk", color: ChartPalette.steps) {
            if pts.allSatisfy({ $0.1.steps == nil && $0.1.avg7 == nil }) {
                ChartEmpty(symbol: "figure.walk", message: notEnoughDays, height: 120)
            } else {
                KpiGrid {
                    KpiTile(title: "Days with 10k+, last 7", value: "\(atGoal)", unit: "of 7",
                            caption: atGoal >= 5 ? "solid week" : nil, captionColor: .green, color: ChartPalette.steps)
                    KpiTile(title: "7-day average", value: avg.map { Fmt.number($0, maxFraction: 0) } ?? "–", unit: "steps")
                }
                Chart {
                    ForEach(pts, id: \.0) { date, p in
                        if let steps = p.steps {
                            BarMark(x: .value("Date", date, unit: .day), y: .value("Steps", steps))
                                .foregroundStyle(ChartPalette.steps.opacity(0.22))
                                .cornerRadius(2)
                        }
                    }
                    ForEach(pts.filter { $0.1.avg7 != nil }, id: \.0) { date, p in
                        LineMark(x: .value("Date", date), y: .value("7-day average", p.avg7 ?? 0), series: .value("Series", "avg"))
                            .foregroundStyle(ChartPalette.steps)
                            .interpolationMethod(.monotone)
                            .lineStyle(StrokeStyle(lineWidth: 3, lineCap: .round))
                    }
                    RuleMark(y: .value("Goal", Self.goal))
                        .foregroundStyle(.secondary.opacity(0.7))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                        .annotation(position: .top, alignment: .leading, spacing: 2) { Text("10k").font(.caption2).foregroundStyle(.secondary) }
                    if let sel {
                        RuleMark(x: .value("Date", sel.0)).foregroundStyle(.secondary.opacity(0.4))
                            .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                                DayCallout(day: sel.1.date, lines: ["\(sel.1.steps.map { Fmt.number($0, maxFraction: 0) } ?? "–") steps",
                                                                     "7-day avg \(sel.1.avg7.map { Fmt.number($0, maxFraction: 0) } ?? "–")"])
                            }
                    }
                }
                .chartXSelection(value: $selected)
                .chartXAxis { dateAxis() }
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.number(d / 1000, maxFraction: 0) + "k") } } } }
                .frame(height: 180)
                .accessibilityLabel("Daily steps and 7-day average over \(pts.count) days")
                .accessibilityValue("7-day average \(avg.map { Fmt.number($0, maxFraction: 0) } ?? "unknown") steps, \(atGoal) of the last 7 days above 10,000")
            }
        }
    }
}
