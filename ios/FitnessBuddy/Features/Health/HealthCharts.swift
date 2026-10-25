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

// MARK: - Body Battery

struct BodyBatteryCard: View {
    let days: [HealthDayDTO]

    var body: some View {
        let pts = days.compactMap { d -> (Date, Double, Double)? in
            guard let date = DayKey.date(d.date), let lo = d.bodyBatteryLow, let hi = d.bodyBatteryHigh else { return nil }
            return (date, lo, hi)
        }
        ChartCard(title: "Body Battery", subtitle: "Daily low to high.", symbol: "battery.100percent.bolt", color: ChartPalette.battery) {
            if pts.isEmpty {
                ChartEmpty(symbol: "battery.50percent", message: notEnoughDays, height: 120)
            } else {
                Chart(pts, id: \.0) { date, lo, hi in
                    BarMark(x: .value("Date", date, unit: .day), yStart: .value("Low", lo), yEnd: .value("High", hi))
                        .foregroundStyle(LinearGradient(colors: [ChartPalette.battery.opacity(0.4), ChartPalette.battery], startPoint: .bottom, endPoint: .top))
                        .cornerRadius(3)
                }
                .chartXAxis { dateAxis() }
                .chartYScale(domain: 0...100)
                .chartYAxis { AxisMarks(position: .leading, values: [0, 50, 100]) }
                .frame(height: 150)
                .accessibilityLabel("Body Battery daily range over \(pts.count) days")
                .accessibilityValue("Latest \(Int(pts.last?.1 ?? 0)) to \(Int(pts.last?.2 ?? 0))")
            }
        }
    }
}

// MARK: - Steps

struct StepsCard: View {
    let days: [HealthDayDTO]

    var body: some View {
        let pts = days.compactMap { d -> (Date, Double)? in
            guard let date = DayKey.date(d.date), let s = d.steps else { return nil }
            return (date, s)
        }
        ChartCard(title: "Steps", symbol: "figure.walk", color: ChartPalette.steps) {
            if pts.isEmpty {
                ChartEmpty(symbol: "figure.walk", message: notEnoughDays, height: 120)
            } else {
                Chart(pts, id: \.0) { date, steps in
                    BarMark(x: .value("Date", date, unit: .day), y: .value("Steps", steps))
                        .foregroundStyle(ChartPalette.steps.gradient)
                        .cornerRadius(3)
                }
                .chartXAxis { dateAxis() }
                .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.number(d / 1000, maxFraction: 0) + "k") } } } }
                .frame(height: 140)
                .accessibilityLabel("Daily steps over \(pts.count) days")
                .accessibilityValue("Average \(Fmt.number(pts.map(\.1).reduce(0, +) / Double(pts.count), maxFraction: 0)) steps")
            }
        }
    }
}

// MARK: - Readiness calendar

struct ReadinessCalendarCard: View {
    let days: [HealthDayDTO]
    let count: Int

    var body: some View {
        let strip = HealthTransforms.readinessStrip(days, count: count)
        let known = strip.filter { $0.level != .unknown }.count
        ChartCard(title: "Readiness", subtitle: "One dot per day, oldest first.", symbol: "circle.grid.3x3.fill", color: Domain.recovery.color) {
            if known == 0 {
                ChartEmpty(symbol: "circle.dotted", message: "Readiness appears once your watch data has synced.", height: 80)
            } else {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 15), spacing: 6) {
                    ForEach(strip, id: \.date) { item in
                        Circle()
                            .fill(item.level == .unknown ? Color.secondary.opacity(0.18) : item.level.color)
                            .aspectRatio(1, contentMode: .fit)
                            .accessibilityHidden(true)
                    }
                }
                HStack(spacing: 12) {
                    dot(.green, "Ready"); dot(.yellow, "Easier"); dot(.red, "Recover"); dot(Color.secondary.opacity(0.3), "No data")
                }
                .font(.caption2)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Readiness calendar, last \(count) days")
        .accessibilityValue("\(strip.filter { $0.level == .green }.count) green, \(strip.filter { $0.level == .yellow }.count) yellow, \(strip.filter { $0.level == .red }.count) red days")
    }

    private func dot(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 4) { Circle().fill(color).frame(width: 8, height: 8); Text(text).foregroundStyle(.secondary) }
    }
}
