import SwiftUI

/// Readiness on the Health tab: the headline, then HRV, resting HR and sleep each against its own baseline (from the
/// real `ReadinessSummary`). Tapping opens the full explanation sheet.
struct ReadinessBreakdownCard: View {
    let summary: ReadinessSummary?
    /// Grey context on the right of the header ("Today").
    var time: String?
    @State private var showDetails: Bool

    init(summary: ReadinessSummary?, time: String? = nil, startOpen: Bool = false) {
        self.summary = summary
        self.time = time
        _showDetails = State(initialValue: startOpen && summary != nil)
    }

    private var level: ReadinessLevel { summary?.level ?? .unknown }

    var body: some View {
        Button { if summary != nil { showDetails = true } } label: {
            Card {
                HStack(spacing: 6) {
                    Label("Readiness", systemImage: Domain.readiness.symbol)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Domain.readiness.color)
                    Spacer(minLength: 8)
                    if let time, summary != nil { Text(time).font(.subheadline).foregroundStyle(.secondary) }
                    if summary != nil { DisclosureChevron() }
                }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(summary?.headline ?? ReadinessLevel.unknown.title)
                        .font(.system(.title, design: .rounded).weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)
                    Image(systemName: "\(level.symbol).circle.fill")
                        .font(.title3)
                        .foregroundStyle(level.color)
                        .accessibilityHidden(true)
                }
                if let line = summary?.details.first {
                    Text(line).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                } else if summary == nil {
                    Text("Shows up once your watch data has synced.").font(.subheadline).foregroundStyle(.secondary)
                }
                if let metrics = summary?.metrics, !metrics.isEmpty {
                    VStack(spacing: 0) {
                        ForEach(Array(metrics.enumerated()), id: \.element.id) { i, m in
                            if i > 0 { Divider() }
                            BaselineRow(metric: m)
                        }
                    }
                    .padding(.top, 2)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Readiness: \(summary?.headline ?? "no data yet")")
        .accessibilityValue((summary?.metrics ?? []).map(\.spokenDescription).joined(separator: ". "))
        .accessibilityHint(summary == nil ? "" : "Shows the details")
        .sheet(isPresented: $showDetails) {
            if let summary { ReadinessDetailSheet(summary: summary) }
        }
    }
}

/// One readiness metric: name and baseline on the left, today's value and a status pill on the right.
private struct BaselineRow: View {
    let metric: ReadinessMetricDTO

    private var symbol: String {
        switch metric.key {
        case "hrv": MetricCatalog.hrv.symbol
        case "rhr": MetricCatalog.restingHR.symbol
        case "sleep": MetricCatalog.sleep.symbol
        default: "waveform.path.ecg"
        }
    }

    private var domain: Domain { metric.key == "sleep" ? .sleep : .heart }

    private var status: (text: String, symbol: String) {
        switch metric.resolvedStatus {
        case .ok: ("In Range", "checkmark")
        case .warn: ("A Bit Off", "exclamationmark")
        case .bad: ("Off", "exclamationmark.2")
        case .unknown: ("No Baseline", "minus")
        }
    }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbol).foregroundStyle(domain.color).frame(width: 22).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(metric.displayLabel).font(.body.weight(.medium)).lineLimit(1)
                Text(metric.baselineText.map { "Baseline \($0)" } ?? "No baseline yet")
                    .font(.footnote).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 3) {
                MetricText(value: metric.valueText, unit: metric.unit, size: .title3)
                Chip(text: status.text, color: metric.resolvedStatus.color, symbol: status.symbol)
            }
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(metric.spokenDescription)
    }
}
